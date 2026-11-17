import 'dart:math' show min;

import 'package:flutter/material.dart';

import 'lot.dart';
import 'theme.dart';

/// Bays as the drone sees them: painted stall lines, a car in every taken bay,
/// and a lit bay wherever one is free. The asphalt is the widget behind it.
class LotPainter extends CustomPainter {
  LotPainter({
    required this.bays,
    required this.bounds,
    this.selectedId,
    this.live = true,
  });

  final List<Bay> bays;
  final Rect bounds;
  final String? selectedId;
  final bool live;

  // Most parked cars are white, silver or grey. A fixed mix per bay keeps the
  // lot looking real without inventing colours.
  static const _tones = [
    Color(0xFFF1F3F5),
    Color(0xFFC3C9D1),
    Color(0xFFE3E7EB),
    Color(0xFFA2AAB5),
    Color(0xFFD6DBE1),
  ];

  @override
  void paint(Canvas canvas, Size size) {
    canvas.scale(size.width / bounds.width);
    canvas.translate(-bounds.left, -bounds.top);

    final lit = Paint()..color = Palette.free.withValues(alpha: 0.13);
    final glow = Paint()
      ..color = Palette.free.withValues(alpha: 0.45)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 2.5);
    final lamp = Paint()..color = Palette.free;
    final lines = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2
      ..strokeCap = StrokeCap.square
      ..color = Palette.linePaint.withValues(alpha: live ? 0.85 : 0.45);

    for (final bay in bays) {
      final heading = carHeading(bay, bays);
      // Without live data, keep the cars we last saw but switch the lights off.
      if (!bay.occupied && live) {
        final c = bay.frame.centre;
        canvas.drawPath(bay.path, lit);
        canvas.drawCircle(c, bay.frame.width * 0.3, glow);
        canvas.drawCircle(c, bay.frame.width * 0.12, lamp);
      }
      _stallLines(canvas, bay, heading, lines);
      if (bay.occupied) _car(canvas, bay, heading);
    }

    final selected = bays.where((b) => b.id == selectedId).firstOrNull;
    if (selected != null) {
      canvas.drawPath(
        selected.path,
        Paint()..color = Palette.accentNight.withValues(alpha: 0.22),
      );
      canvas.drawPath(
        selected.path,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.6
          ..color = Palette.accentNight,
      );
    }
  }

  /// Painted the way real bays are: the two dividers and the line at the
  /// back, open on the lane side where cars drive in.
  void _stallLines(Canvas canvas, Bay bay, double heading, Paint lines) {
    final f = bay.frame;
    final ahead = Offset.fromDirection(heading);
    final p = bay.points;
    for (var i = 0; i < p.length; i++) {
      final a = p[i], b = p[(i + 1) % p.length];
      final mid = (a + b) / 2 - f.centre;
      final isOpenEnd =
          (b - a).distance < f.length * 0.75 &&
          mid.dx * ahead.dx + mid.dy * ahead.dy < 0;
      if (!isOpenEnd) canvas.drawLine(a, b, lines);
    }
  }

  void _car(Canvas canvas, Bay bay, double heading) {
    final f = bay.frame;
    final l = f.length * 0.8;
    final w = min(f.width * 0.72, l * 0.47);
    var tone =
        _tones[bay.code.codeUnits.fold(0, (a, c) => a * 31 + c) %
            _tones.length];
    var glass = Palette.glass;
    if (!live) {
      // Dimmed into the asphalt, but opaque, so the roof still hides the glass.
      tone = Color.lerp(tone, Palette.asphalt, 0.55)!;
      glass = Color.lerp(glass, Palette.asphalt, 0.55)!;
    }

    canvas.save();
    canvas.translate(f.centre.dx, f.centre.dy);
    canvas.rotate(heading); // nose points along +x
    final body = RRect.fromLTRBAndCorners(
      -l / 2,
      -w / 2,
      l / 2,
      w / 2,
      topRight: Radius.circular(w * 0.42),
      bottomRight: Radius.circular(w * 0.42),
      topLeft: Radius.circular(w * 0.3),
      bottomLeft: Radius.circular(w * 0.3),
    );
    canvas.drawRRect(
      body,
      Paint()
        ..color = Colors.black.withValues(alpha: live ? 0.5 : 0.2)
        ..maskFilter = MaskFilter.blur(BlurStyle.normal, w * 0.14),
    );
    canvas.drawRRect(body, Paint()..color = tone);
    // Glasshouse first, then the roof over it: what shows is the windscreen,
    // the rear window and a sliver of side glass, as seen from above.
    canvas.drawRRect(
      RRect.fromLTRBR(
        -l * 0.31,
        -w * 0.4,
        l * 0.27,
        w * 0.4,
        Radius.circular(w * 0.24),
      ),
      Paint()..color = glass,
    );
    canvas.drawRRect(
      RRect.fromLTRBR(
        -l * 0.19,
        -w * 0.36,
        l * 0.05,
        w * 0.36,
        Radius.circular(w * 0.1),
      ),
      Paint()..color = tone,
    );
    canvas.restore();
  }

  @override
  bool shouldRepaint(LotPainter old) =>
      old.bays != bays ||
      old.bounds != bounds ||
      old.selectedId != selectedId ||
      old.live != live;
}
