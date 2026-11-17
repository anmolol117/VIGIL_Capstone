import 'dart:math' show pi;

import 'package:flutter/material.dart';

/// One row of the `lot_occupancy` view.
///
/// The status thresholds live in that SQL view, not here, so every client agrees
/// and a threshold change needs no app release. This file only decides how each
/// status is shown.
class Lot {
  const Lot({
    required this.id,
    required this.name,
    required this.status,
    this.occupied,
    this.capacity,
    this.free,
    this.pctFull,
    this.updatedAt,
  });

  factory Lot.fromJson(Map<String, dynamic> json) => Lot(
    id: json['lot_id'] as String,
    name: json['name'] as String,
    status: json['status'] as String? ?? 'stale',
    occupied: (json['occupied'] as num?)?.toInt(),
    capacity: (json['capacity'] as num?)?.toInt(),
    free: (json['free'] as num?)?.toInt(),
    pctFull: (json['pct_full'] as num?)?.toInt(),
    updatedAt: json['updated_at'] == null
        ? null
        : DateTime.parse(json['updated_at'] as String),
  );

  final String id;
  final String name;
  final String status;
  final int? occupied;
  final int? capacity;
  final int? free;
  final int? pctFull;
  final DateTime? updatedAt;

  /// False when the camera system hasn't reported recently ('stale').
  bool get isLive => status != 'stale';

  /// True once the lot has ever reported numbers, live or not.
  bool get hasData => occupied != null && capacity != null;
}

/// One parking bay: its real geometry (pixels in the lot's reference frame, the
/// hand-marked bays) and whether it is occupied right now.
class Bay {
  Bay({
    required this.id,
    required this.code,
    required this.points,
    required this.occupied,
  });

  factory Bay.fromJson(Map<String, dynamic> json) {
    // Embedded one-to-one row; PostgREST may return it as an object, a
    // one-item list, or null when no state has been written for the bay.
    final raw = json['slot_status'];
    final state = raw is List ? (raw.isEmpty ? null : raw.first) : raw;
    // An angled camera sees bays in perspective; such lots also store each
    // bay's outline on the top-down ground plane, which is what a map needs.
    final outline = (json['map_polygon'] ?? json['polygon']) as List;
    return Bay(
      id: json['id'] as String,
      code: json['code'] as String,
      points: [
        for (final p in outline)
          Offset((p[0] as num).toDouble(), (p[1] as num).toDouble()),
      ],
      // No state row yet means the camera system has never seen it taken.
      occupied:
          (state as Map<String, dynamic>?)?['is_occupied'] as bool? ?? false,
    );
  }

  final String id;
  final String code;
  final List<Offset> points;
  final bool occupied;

  /// The bay number people use: PARKING-A-121 -> 121.
  String get number => code.split('-').last;

  late final Path path = Path()..addPolygon(points, true);
  late final Rect rect = path.getBounds();

  /// Centre, long-axis angle, and size, from the first two edges.
  // ponytail: assumes the hand-marked quadrilaterals every lot uses today.
  late final ({Offset centre, double angle, double length, double width})
  frame = () {
    final e1 = points[1] - points[0];
    final e2 = points[2] - points[1];
    final (long, short) = e1.distance >= e2.distance ? (e1, e2) : (e2, e1);
    final centre = points.reduce((a, b) => a + b) / points.length.toDouble();
    return (
      centre: centre,
      angle: long.direction,
      length: long.distance,
      width: short.distance,
    );
  }();
}

/// The way a parked car faces: nose-in, toward the bay it backs onto in a
/// double row. A bay with nothing ahead faces the other way.
double carHeading(Bay bay, List<Bay> bays) {
  final f = bay.frame;
  final ahead = f.centre + Offset.fromDirection(f.angle, f.length * 0.8);
  final backsOnto = bays.any(
    (b) => b != bay && b.rect.contains(ahead) && b.path.contains(ahead),
  );
  return backsOnto ? f.angle : f.angle + pi;
}

/// The area the bays occupy, with a small margin, so the map fits the screen.
Rect baysBounds(List<Bay> bays, {double margin = 6}) {
  final all = bays.expand((b) => b.points);
  if (all.isEmpty) return Rect.zero;
  var r = Rect.fromPoints(all.first, all.first);
  for (final p in all) {
    r = r.expandToInclude(Rect.fromPoints(p, p));
  }
  return r.inflate(margin);
}

typedef StatusStyle = ({String label, Color color});

StatusStyle statusStyle(String status) => switch (status) {
  'wide_open' => (label: 'Wide open', color: const Color(0xFF248A3D)),
  'plenty' => (label: 'Plenty of space', color: const Color(0xFF0F8F9F)),
  'filling_up' => (label: 'Filling up', color: const Color(0xFFC58A00)),
  'almost_full' => (label: 'Almost full', color: const Color(0xFFE06A00)),
  'full' => (label: 'Full', color: const Color(0xFFD70015)),
  _ => (label: 'No live data', color: const Color(0xFF8E8E93)),
};

String freeLabel(int free) => free == 1 ? '1 space free' : '$free spaces free';

/// "Updated 12s ago". The device clock can drift from the server's, so a
/// timestamp slightly in the future reads as "just now" rather than negative.
String updatedAgo(DateTime? updatedAt, DateTime now) {
  if (updatedAt == null) return 'No data yet';
  final age = now.difference(updatedAt);
  if (age.inSeconds < 5) return 'Updated just now';
  if (age.inMinutes < 1) return 'Updated ${age.inSeconds}s ago';
  if (age.inHours < 1) return 'Updated ${age.inMinutes}m ago';
  if (age.inDays < 1) return 'Updated ${age.inHours}h ago';
  return 'Updated ${age.inDays}d ago';
}
