import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Visual language: the VIGIL theme (REDESIGN_PLAN.md section 2). iOS grouped
/// greys, system blue as the single accent for controls, and system green for a
/// car park's "bay free" indicator light. The lot map stays dark asphalt with
/// painted white stall lines.
abstract final class Palette {
  static const concrete = Color(0xFFF2F2F7); // app ground, light
  static const concreteNight = Color(0xFF000000); // app ground, dark
  static const card = Color(0xFFFFFFFF);
  static const cardNight = Color(0xFF1C1C1E);
  static const ink = Color(0xFF1D1D1F);
  static const inkNight = Color(0xFFF5F5F7);
  static const asphalt = Color(0xFF2C2C2E);
  static const linePaint = Color(0xFFF2F2F7); // stall and lane markings
  static const accent = Color(0xFF007AFF); // system blue
  static const accentNight = Color(0xFF0A84FF);
  static const free = Color(0xFF30D158); // bay-free indicator green
  static const glass = Color(0xFF1C1C1E);
}

const tabular = [FontFeature.tabularFigures()];

ThemeData buildTheme(Brightness brightness) {
  final night = brightness == Brightness.dark;
  final ink = night ? Palette.inkNight : Palette.ink;
  final card = night ? Palette.cardNight : Palette.card;
  final muted = night ? const Color(0xFF98989D) : const Color(0xFF6E6E73);
  final accent = night ? Palette.accentNight : Palette.accent;

  final scheme =
      ColorScheme.fromSeed(
        seedColor: Palette.accent,
        brightness: brightness,
      ).copyWith(
        primary: accent,
        onPrimary: Colors.white,
        surface: card,
        onSurface: ink,
        onSurfaceVariant: muted,
        surfaceTint: Colors.transparent,
        surfaceContainerHighest: night
            ? const Color(0xFF2C2C2E)
            : const Color(0xFFE5E5EA),
        outlineVariant: night
            ? const Color(0xFF38383A)
            : const Color(0xFFE5E5EA),
      );

  final base = ThemeData(colorScheme: scheme, brightness: brightness);
  final text = base.textTheme
      .apply(bodyColor: ink, displayColor: ink)
      .copyWith(
        displaySmall: base.textTheme.displaySmall?.copyWith(
          fontWeight: FontWeight.w700,
          letterSpacing: -0.8,
          color: ink,
        ),
        headlineMedium: base.textTheme.headlineMedium?.copyWith(
          fontWeight: FontWeight.w700,
          letterSpacing: -0.4,
          color: ink,
        ),
        titleLarge: base.textTheme.titleLarge?.copyWith(
          fontWeight: FontWeight.w600,
          color: ink,
        ),
      );

  return base.copyWith(
    scaffoldBackgroundColor: night ? Palette.concreteNight : Palette.concrete,
    textTheme: text,
    appBarTheme: AppBarTheme(
      backgroundColor: Colors.transparent,
      surfaceTintColor: Colors.transparent,
      scrolledUnderElevation: 0,
      // A transparent bar reads as dark to Flutter, so say which icons to use.
      systemOverlayStyle: night
          ? SystemUiOverlayStyle.light
          : SystemUiOverlayStyle.dark,
      foregroundColor: ink,
      centerTitle: false,
      titleTextStyle: text.titleLarge,
    ),
    cardTheme: CardThemeData(
      color: card,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        // Apple-style grouped cards: the colour separates them, no outline.
        borderRadius: BorderRadius.circular(22),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: accent,
        foregroundColor: Colors.white,
        disabledBackgroundColor: accent.withValues(alpha: 0.45),
        minimumSize: const Size.fromHeight(52),
        shape: const StadiumBorder(),
        textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 17),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: accent,
        textStyle: const TextStyle(fontWeight: FontWeight.w600),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: card,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
      labelStyle: TextStyle(color: muted),
      floatingLabelStyle: TextStyle(color: ink, fontWeight: FontWeight.w600),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: scheme.outlineVariant),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: scheme.outlineVariant),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: accent, width: 2),
      ),
    ),
    progressIndicatorTheme: ProgressIndicatorThemeData(color: ink),
  );
}

/// The small status pill used on lot cards and the map header.
class StatusPill extends StatelessWidget {
  const StatusPill({super.key, required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 5),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 6),
          Text(
            label,
            style: TextStyle(
              color: color,
              fontWeight: FontWeight.w700,
              fontSize: 12.5,
            ),
          ),
        ],
      ),
    );
  }
}
