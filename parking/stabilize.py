"""Register drone frames against a lot's reference image.

A hovering drone drifts - GPS wander, wind, yaw, altitude breathing - so raw
image coordinates are not a stable frame of reference. Slot polygons drawn on
frame 1 point at the wrong asphalt by frame 900, and ByteTrack's constant
velocity model reads global camera motion as every vehicle accelerating at once.

Both problems go away if every frame is mapped back onto one fixed reference
image first. Matching is always done directly against that reference rather than
frame to frame: chaining frame to frame accumulates drift, direct-to-reference
cannot.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Registration:
    """Result of aligning one frame to the reference image."""

    H: np.ndarray  # maps current frame -> reference frame
    ok: bool  # False => H is a stale fallback and this frame's counts are suspect
    inliers: int
    inlier_ratio: float


def warp_boxes(H: np.ndarray, xyxy: np.ndarray) -> np.ndarray:
    """Warp xyxy boxes through H, returning the axis-aligned box of each result.

    A rectangle under a homography is a general quadrilateral, so this is an
    approximation - but near-nadir drone views carry little rotation, and the
    error is far smaller than the drift it removes.
    """
    if len(xyxy) == 0:
        return np.empty((0, 4), dtype=np.float32)

    x1, y1, x2, y2 = xyxy[:, 0], xyxy[:, 1], xyxy[:, 2], xyxy[:, 3]
    corners = np.stack(
        [
            np.stack([x1, y1], axis=1),
            np.stack([x2, y1], axis=1),
            np.stack([x2, y2], axis=1),
            np.stack([x1, y2], axis=1),
        ],
        axis=1,
    ).astype(np.float32)  # (N, 4, 2)

    flat = corners.reshape(-1, 1, 2)
    warped = cv2.perspectiveTransform(flat, H.astype(np.float32)).reshape(-1, 4, 2)

    return np.stack(
        [
            warped[:, :, 0].min(axis=1),
            warped[:, :, 1].min(axis=1),
            warped[:, :, 0].max(axis=1),
            warped[:, :, 1].max(axis=1),
        ],
        axis=1,
    ).astype(np.float32)


class Stabilizer:
    """Aligns frames to a fixed reference image via ORB + RANSAC homography."""

    def __init__(
        self,
        reference_bgr: np.ndarray,
        *,
        max_features: int = 2000,
        ratio_test: float = 0.75,
        ransac_reproj_threshold: float = 4.0,
        min_inliers: int = 25,
        min_inlier_ratio: float = 0.15,
        max_scale_change: float = 1.6,
        mask_dilate_px: int = 8,
    ) -> None:
        self.ratio_test = ratio_test
        self.ransac_reproj_threshold = ransac_reproj_threshold
        self.min_inliers = min_inliers
        self.min_inlier_ratio = min_inlier_ratio
        self.max_scale_change = max_scale_change
        self.mask_dilate_px = mask_dilate_px

        self.orb = cv2.ORB_create(nfeatures=max_features)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

        # Reference features are computed once and reused for every frame.
        ref_gray = cv2.cvtColor(reference_bgr, cv2.COLOR_BGR2GRAY)
        self.ref_shape = ref_gray.shape
        self.ref_kp, self.ref_desc = self.orb.detectAndCompute(ref_gray, None)
        if self.ref_desc is None or len(self.ref_kp) < self.min_inliers:
            raise ValueError(
                f"reference image yielded only {len(self.ref_kp)} ORB features - "
                "it is too smooth or blurred to register against; recapture it"
            )

        self.last_H = np.eye(3, dtype=np.float64)

    def register(self, frame_bgr: np.ndarray, ignore_boxes=()) -> Registration:
        """Align one frame. `ignore_boxes` are xyxy regions to exclude from matching.

        Passing the previous frame's vehicle boxes matters: features found on cars
        describe things that move, which is exactly what must not drive the estimate.
        """
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        kp, desc = self.orb.detectAndCompute(gray, self._static_mask(gray.shape, ignore_boxes))

        if desc is None or len(kp) < 4:
            return self._fallback()

        pairs = self.matcher.knnMatch(desc, self.ref_desc, k=2)
        good = [
            p[0] for p in pairs if len(p) == 2 and p[0].distance < self.ratio_test * p[1].distance
        ]
        if len(good) < self.min_inliers:
            return self._fallback()

        src = np.float32([kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([self.ref_kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, self.ransac_reproj_threshold)
        inliers = int(mask.sum()) if mask is not None else 0
        ratio = inliers / len(good)

        if (
            inliers < self.min_inliers
            or ratio < self.min_inlier_ratio
            or not self._plausible(H, gray.shape)
        ):
            return self._fallback(inliers, ratio)

        self.last_H = H
        return Registration(H, True, inliers, ratio)

    def _static_mask(self, shape, boxes):
        """255 where features may be taken, 0 over known moving objects."""
        if len(boxes) == 0:
            return None
        mask = np.full(shape, 255, dtype=np.uint8)
        pad = self.mask_dilate_px
        h, w = shape
        for x1, y1, x2, y2 in np.asarray(boxes, dtype=np.float32):
            mask[
                max(0, int(y1) - pad) : min(h, int(y2) + pad),
                max(0, int(x1) - pad) : min(w, int(x2) + pad),
            ] = 0
        # An all-zero mask would starve the matcher; better to accept car features.
        return mask if mask.any() else None

    def _plausible(self, H, shape) -> bool:
        """Reject homographies that imply a physically impossible camera jump."""
        if H is None or not np.all(np.isfinite(H)):
            return False

        det = float(np.linalg.det(H[:2, :2]))
        if abs(det) < 1e-9:
            return False
        scale = float(np.sqrt(abs(det)))
        if not (1.0 / self.max_scale_change) <= scale <= self.max_scale_change:
            return False

        # A fold-over produces a non-convex quad - a sure sign of a bad fit.
        h, w = shape
        corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        warped = cv2.perspectiveTransform(corners, H.astype(np.float32))
        return bool(cv2.isContourConvex(warped.astype(np.float32)))

    def _fallback(self, inliers: int = 0, ratio: float = 0.0) -> Registration:
        """Reuse the last good H rather than writing garbage coordinates."""
        return Registration(self.last_H, False, inliers, ratio)
