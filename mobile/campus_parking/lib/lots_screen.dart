import 'dart:async';

import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'lot.dart';
import 'lot_layout_screen.dart';
import 'theme.dart';

class LotsScreen extends StatefulWidget {
  const LotsScreen({super.key});

  @override
  State<LotsScreen> createState() => _LotsScreenState();
}

class _LotsScreenState extends State<LotsScreen> with WidgetsBindingObserver {
  List<Lot> _lots = const [];
  bool _loading = true;
  bool _refreshFailed = false;
  RealtimeChannel? _channel;
  Timer? _ticker;
  Timer? _debounce;
  int _ticks = 0;

  SupabaseClient get _client => Supabase.instance.client;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load();
    // Realtime is a signal, not the data: any new snapshot means "refetch the
    // view". One tiny query, it keeps the status logic in SQL, and it self-heals
    // if an event is missed. Debounced so a burst of bay changes costs one fetch.
    _channel = _client
        .channel('lot-occupancy')
        .onPostgresChanges(
          event: PostgresChangeEvent.insert,
          schema: 'public',
          table: 'parking_snapshots',
          callback: (_) {
            _debounce?.cancel();
            _debounce = Timer(const Duration(milliseconds: 500), _load);
          },
        )
        .subscribe();
    // Tick every 5 s so "Updated 12s ago" stays true, and refetch every 30 s so
    // a lot whose camera went quiet turns grey even with no events arriving.
    _ticker = Timer.periodic(const Duration(seconds: 5), (_) {
      if (++_ticks % 6 == 0) {
        _load();
      } else if (mounted) {
        setState(() {});
      }
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _load();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _ticker?.cancel();
    _debounce?.cancel();
    if (_channel != null) _client.removeChannel(_channel!);
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final rows = await _client.from('lot_occupancy').select().order('name');
      if (!mounted) return;
      setState(() {
        _lots = rows.map(Lot.fromJson).toList();
        _loading = false;
        _refreshFailed = false;
      });
    } catch (_) {
      // Keep showing the last good numbers; just say they may be out of date.
      if (mounted) {
        setState(() {
          _loading = false;
          _refreshFailed = true;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    return Scaffold(
      appBar: AppBar(
        toolbarHeight: 72,
        title: Text(
          'Parking',
          style: Theme.of(context).textTheme.headlineMedium,
        ),
        actions: [
          IconButton(
            tooltip: 'Sign out',
            icon: const Icon(Icons.logout_rounded),
            onPressed: () => _client.auth.signOut(),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
                children: [
                  if (_refreshFailed) const _OfflineBanner(),
                  if (_lots.isEmpty)
                    const Padding(
                      padding: EdgeInsets.only(top: 120),
                      child: Center(child: Text('No parking lots yet.')),
                    ),
                  for (final lot in _lots) _LotCard(lot: lot, now: now),
                ],
              ),
            ),
    );
  }
}

class _LotCard extends StatelessWidget {
  const _LotCard({required this.lot, required this.now});

  final Lot lot;
  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final style = statusStyle(lot.status);
    final muted = theme.colorScheme.onSurfaceVariant;

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<void>(builder: (_) => LotLayoutScreen(lot: lot)),
        ),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 18, 20, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(lot.name, style: theme.textTheme.titleLarge),
                  ),
                  StatusPill(label: style.label, color: style.color),
                ],
              ),
              const SizedBox(height: 16),
              if (lot.hasData) ...[
                Row(
                  crossAxisAlignment: CrossAxisAlignment.baseline,
                  textBaseline: TextBaseline.alphabetic,
                  children: [
                    Text(
                      '${lot.occupied}',
                      style: theme.textTheme.displaySmall?.copyWith(
                        fontFeatures: tabular,
                        color: lot.isLive ? null : muted,
                      ),
                    ),
                    Text(
                      ' / ${lot.capacity} parked',
                      style: theme.textTheme.titleMedium?.copyWith(
                        color: muted,
                        fontFeatures: tabular,
                      ),
                    ),
                    const Spacer(),
                    Text(
                      freeLabel(lot.free ?? 0),
                      style: theme.textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                        fontFeatures: tabular,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                _StallBar(
                  full: (lot.pctFull ?? 0) / 100,
                  // A stale lot keeps its last numbers but drops its colour,
                  // so it never looks live when it isn't.
                  color: lot.isLive ? style.color : muted,
                ),
              ] else
                Text(
                  'Waiting for data from the camera system.',
                  style: theme.textTheme.bodyLarge?.copyWith(color: muted),
                ),
              const SizedBox(height: 14),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      lot.pctFull == null
                          ? updatedAgo(lot.updatedAt, now)
                          : '${lot.pctFull}% full  ·  ${updatedAgo(lot.updatedAt, now)}',
                      style: theme.textTheme.bodySmall?.copyWith(color: muted),
                    ),
                  ),
                  Text(
                    'Bay map',
                    style: theme.textTheme.bodySmall?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const Icon(Icons.chevron_right_rounded, size: 18),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// How full the lot is, drawn as a strip of asphalt with painted stall lines.
class _StallBar extends StatelessWidget {
  const _StallBar({required this.full, required this.color});

  final double full;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 14,
      width: double.infinity,
      child: CustomPaint(painter: _StallBarPainter(full, color)),
    );
  }
}

class _StallBarPainter extends CustomPainter {
  _StallBarPainter(this.full, this.color);

  final double full;
  final Color color;

  static const _stalls = 20;

  @override
  void paint(Canvas canvas, Size size) {
    final strip = RRect.fromRectAndRadius(
      Offset.zero & size,
      const Radius.circular(5),
    );
    canvas.clipRRect(strip);
    canvas.drawRRect(strip, Paint()..color = Palette.asphalt);
    canvas.drawRect(
      Rect.fromLTWH(0, 0, size.width * full.clamp(0, 1), size.height),
      Paint()..color = color,
    );
    final line = Paint()
      ..color = Palette.linePaint.withValues(alpha: 0.55)
      ..strokeWidth = 1.5;
    for (var i = 1; i < _stalls; i++) {
      final x = size.width * i / _stalls;
      canvas.drawLine(Offset(x, 3), Offset(x, size.height - 3), line);
    }
  }

  @override
  bool shouldRepaint(_StallBarPainter old) =>
      old.full != full || old.color != color;
}

class _OfflineBanner extends StatelessWidget {
  const _OfflineBanner();

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: Palette.accent.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(14),
      ),
      child: const Row(
        children: [
          Icon(Icons.cloud_off_rounded, size: 18),
          SizedBox(width: 10),
          Expanded(
            child: Text("Couldn't refresh. Showing the last known numbers."),
          ),
        ],
      ),
    );
  }
}
