import 'dart:math';

import 'package:flutter/material.dart';

// The VIGIL theme (REDESIGN_PLAN.md section 2): iOS grouped greys, near-black
// text, system blue for everyday actions, and red only where something is
// urgent (a speeding car, being on duty, responding). This red on white is
// about 5.5:1 contrast. Dark mode follows the phone's setting and uses the iOS
// dark system colours; screens read the current set with Palette.of(context).
class Palette {
  const Palette({
    required this.brightness,
    required this.bg,
    required this.paper,
    required this.ink,
    required this.muted,
    required this.line,
    required this.red,
    required this.redWash,
    required this.blue,
  });

  final Brightness brightness;
  final Color bg, paper, ink, muted, line, red, redWash, blue;

  static const light = Palette(
    brightness: Brightness.light,
    bg: Color(0xFFF2F2F7),
    paper: Color(0xFFFFFFFF),
    ink: Color(0xFF1D1D1F),
    muted: Color(0xFF6E6E73),
    line: Color(0xFFD1D1D6),
    red: Color(0xFFD70015),
    redWash: Color(0xFFFDECEC),
    blue: Color(0xFF007AFF),
  );

  static const dark = Palette(
    brightness: Brightness.dark,
    bg: Color(0xFF000000),
    paper: Color(0xFF1C1C1E),
    ink: Color(0xFFF5F5F7),
    muted: Color(0xFF98989D),
    line: Color(0xFF38383A),
    red: Color(0xFFFF453A),
    redWash: Color(0xFF3A1714),
    blue: Color(0xFF0A84FF),
  );

  static Palette of(BuildContext context) => Theme.of(context).brightness == Brightness.dark ? dark : light;
}

final appTheme = _theme(Palette.light);
final darkTheme = _theme(Palette.dark);

ThemeData _theme(Palette p) => ThemeData(
      useMaterial3: true,
      scaffoldBackgroundColor: p.bg,
      colorScheme: (p.brightness == Brightness.dark ? ColorScheme.dark : ColorScheme.light)(
        primary: p.blue,
        onPrimary: Colors.white,
        surface: p.bg,
        onSurface: p.ink,
        error: p.red,
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: p.bg,
        foregroundColor: p.ink,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: TextStyle(color: p.ink, fontSize: 18, fontWeight: FontWeight.w600),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: p.blue,
          foregroundColor: Colors.white,
          minimumSize: const Size.fromHeight(56),
          shape: const StadiumBorder(),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600, letterSpacing: 0.8),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(foregroundColor: p.blue, minimumSize: const Size(48, 48)),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: p.paper,
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: p.line)),
        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: p.line)),
        focusedBorder:
            OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: p.blue, width: 2)),
      ),
      dividerColor: p.line,
    );

const _compass = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

String direction(num bearingDeg) => _compass[(bearingDeg / 45).round() % 8];

String distance(num metres) {
  final m = (metres / 10).round() * 10; // round first, so 999 m reads "1.0 km", not "1000 m"
  return m < 1000 ? '$m m' : '${(metres / 1000).toStringAsFixed(1)} km';
}

String ago(DateTime t) {
  final s = max(0, DateTime.now().difference(t).inSeconds);
  if (s < 60) return '$s s ago';
  if (s < 3600) return '${s ~/ 60} min ago';
  if (s < 86400) return '${s ~/ 3600} h ago';
  return '${s ~/ 86400} d ago';
}
