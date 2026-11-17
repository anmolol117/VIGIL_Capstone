import 'dart:async';
import 'dart:math' show max, min;

import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'lot.dart';
import 'lot_painter.dart';
import 'theme.dart';

/// A map of the lot built from the real, hand-marked bay geometry: every bay
/// where it actually is, lit when free, a car when taken, updating live.
class LotLayoutScreen extends StatefulWidget {
  const LotLayoutScreen({super.key, required this.lot});

  final Lot lot;

  @override
  State<LotLayoutScreen> createState() => _LotLayoutScreenState();
}

class _LotLayoutScreenState extends State<LotLayoutScreen>
    with WidgetsBindingObserver {
  late Lot _lot = widget.lot;
  List<Bay> _bays = const [];
  Rect _bounds = Rect.zero;
  Bay? _selected;
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
    // Writers update bay states before the lot's snapshot, so a new snapshot
    // for this lot means its bays are already current: refetch on that signal.
    _channel = _client
        .channel('lot-layout-${widget.lot.id}')
        .onPostgresChanges(
          event: PostgresChangeEvent.insert,
          schema: 'public',
          table: 'parking_snapshots',
          filter: PostgresChangeFilter(
            type: PostgresChangeFilterType.eq,
            column: 'lot_id',
            value: widget.lot.id,
          ),
          callback: (_) {
            _debounce?.cancel();
            _debounce = Timer(const Duration(milliseconds: 500), _load);
          },
        )
        .subscribe();
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
      final (bays, lot) = await (
        _client
            .from('parking_slots')
            .select('id, code, polygon, map_polygon, slot_status(is_occupied)')
            .eq('lot_id', widget.lot.id)
            .order('code'),
        _client
            .from('lot_occupancy')
            .select()
            .eq('lot_id', widget.lot.id)
            .maybeSingle(),
      ).wait;
      if (!mounted) return;
      final parsed = bays.map(Bay.fromJson).toList();
      setState(() {
        _bays = parsed;
        _bounds = baysBounds(parsed);
        if (lot != null) _lot = Lot.fromJson(lot);
        // Keep the selection pointing at the bay's fresh state.
        if (_selected != null) {
          _selected = parsed.where((b) => b.id == _selected!.id).firstOrNull;
        }
        _loading = false;
        _refreshFailed = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _refreshFailed = true;
        });
      }
    }
  }

  void _tapAt(Offset local, double scale) {
    final point = local / scale + _bounds.topLeft;
    final hit = _bays.where((b) => b.path.contains(point)).firstOrNull;
    setState(() => _selected = hit);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final muted = theme.colorScheme.onSurfaceVariant;
    final style = statusStyle(_lot.status);
    final free = _bays.where((b) => !b.occupied).length;
    final bay = _selected;

    return Scaffold(
      appBar: AppBar(title: Text(_lot.name)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : SafeArea(
              top: false,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text.rich(
                            TextSpan(
                              children: [
                                TextSpan(
                                  text: '$free',
                                  style: theme.textTheme.displaySmall?.copyWith(
                                    fontFeatures: tabular,
                                    color: _lot.isLive ? null : muted,
                                  ),
                                ),
                                TextSpan(
                                  text: '  free of ${_bays.length}',
                                  style: theme.textTheme.titleMedium?.copyWith(
                                    color: muted,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                        StatusPill(label: style.label, color: style.color),
                      ],
                    ),
                    if (!_lot.isLive || _refreshFailed) ...[
                      const SizedBox(height: 10),
                      _Notice(
                        _refreshFailed
                            ? "Couldn't refresh. Showing the last known layout."
                            : 'No live data. Showing the last known layout.',
                      ),
                    ],
                    const SizedBox(height: 14),
                    Expanded(child: _map()),
                    const SizedBox(height: 12),
                    AnimatedSwitcher(
                      duration: const Duration(milliseconds: 200),
                      child: bay == null
                          ? _Bar(
                              key: const ValueKey('live'),
                              children: [
                                _Dot(_lot.isLive ? Palette.free : muted),
                                const SizedBox(width: 10),
                                Expanded(
                                  child: Text(
                                    updatedAgo(_lot.updatedAt, DateTime.now()),
                                    style: theme.textTheme.bodyMedium,
                                  ),
                                ),
                                Text(
                                  'Tap a bay for its number',
                                  style: theme.textTheme.bodySmall?.copyWith(
                                    color: muted,
                                  ),
                                ),
                              ],
                            )
                          : _Bar(
                              key: ValueKey(bay.id),
                              children: [
                                Text(
                                  'Bay ${bay.number}',
                                  style: theme.textTheme.titleMedium?.copyWith(
                                    fontWeight: FontWeight.w700,
                                    fontFeatures: tabular,
                                  ),
                                ),
                                const SizedBox(width: 12),
                                StatusPill(
                                  label: bay.occupied ? 'Occupied' : 'Free',
                                  color: !_lot.isLive
                                      ? muted
                                      : bay.occupied
                                      ? Palette.asphalt
                                      : statusStyle('wide_open').color,
                                ),
                                const Spacer(),
                                IconButton(
                                  tooltip: 'Close',
                                  visualDensity: VisualDensity.compact,
                                  icon: const Icon(Icons.close_rounded),
                                  onPressed: () =>
                                      setState(() => _selected = null),
                                ),
                              ],
                            ),
                    ),
                  ],
                ),
              ),
            ),
    );
  }

  Widget _map() {
    return ClipRRect(
      borderRadius: BorderRadius.circular(24),
      child: ColoredBox(
        color: Palette.asphalt,
        child: _bays.isEmpty
            ? Center(
                child: Text(
                  'No bays mapped for this lot yet.',
                  style: TextStyle(
                    color: Palette.linePaint.withValues(alpha: 0.7),
                  ),
                ),
              )
            : LayoutBuilder(
                builder: (context, box) {
                  const pad = 16.0;
                  final w = max(0.0, box.maxWidth - 2 * pad);
                  final h = max(0.0, box.maxHeight - 2 * pad);
                  // Real proportions, turned a quarter when that shows the lot
                  // bigger (a wide lot on a tall phone). RotatedBox also turns
                  // taps, so hit-testing stays in the lot's own frame.
                  final upright = min(w / _bounds.width, h / _bounds.height);
                  final turned = min(w / _bounds.height, h / _bounds.width);
                  final scale = max(upright, turned);
                  return InteractiveViewer(
                    maxScale: 8,
                    child: Center(
                      child: RotatedBox(
                        quarterTurns: turned > upright ? 1 : 0,
                        child: GestureDetector(
                          onTapUp: (d) => _tapAt(d.localPosition, scale),
                          child: RepaintBoundary(
                            child: CustomPaint(
                              size: _bounds.size * scale,
                              painter: LotPainter(
                                bays: _bays,
                                bounds: _bounds,
                                selectedId: _selected?.id,
                                live: _lot.isLive,
                              ),
                            ),
                          ),
                        ),
                      ),
                    ),
                  );
                },
              ),
      ),
    );
  }
}

/// The strip under the map: live status, or the tapped bay.
class _Bar extends StatelessWidget {
  const _Bar({super.key, required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 60,
      padding: const EdgeInsets.only(left: 18, right: 8),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: Theme.of(context).colorScheme.outlineVariant),
      ),
      child: Row(children: children),
    );
  }
}

class _Dot extends StatelessWidget {
  const _Dot(this.color);

  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 9,
      height: 9,
      decoration: BoxDecoration(color: color, shape: BoxShape.circle),
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: Palette.accent.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          const Icon(Icons.history_rounded, size: 18),
          const SizedBox(width: 10),
          Expanded(child: Text(text)),
        ],
      ),
    );
  }
}
