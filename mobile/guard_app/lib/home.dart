import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'alert.dart';
import 'backend.dart';
import 'push.dart';
import 'ui.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final guard = Guard();
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    guard.onNewAlert = _announce;
    Push.onOpen = (eventId) => _open(eventId);
    guard.start().then((_) {
      if (guard.registered) Push.register(_onPush);
    });
    _tick = Timer.periodic(const Duration(seconds: 1), (_) => setState(() {})); // keeps "s ago" live
  }

  @override
  void dispose() {
    _tick?.cancel();
    guard.dispose();
    super.dispose();
  }

  void _announce(Alert a) {
    HapticFeedback.heavyImpact();
    Push.ring(a);
    _open(a.eventId);
  }

  Future<void> _onPush(int eventId, bool tapped) async {
    final (a, isNew) = await guard.fromPush(eventId);
    if (a == null) return;
    if (tapped) {
      _open(eventId);
    } else if (isNew) {
      _announce(a);
    }
  }

  void _open(int eventId) {
    if (!mounted) return;
    // One alert screen at a time: a new alert replaces the open one rather
    // than stacking, so Back always returns straight to the list.
    Navigator.of(context)
      ..popUntil((r) => r.isFirst)
      ..push(MaterialPageRoute(builder: (_) => AlertScreen(guard: guard, eventId: eventId)));
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: guard,
      builder: (context, _) {
        final p = Palette.of(context);
        if (!guard.registered) return _NotAGuard(onSignOut: guard.signOut);
        final active = guard.alerts.where((a) => a.active).toList();
        final earlier = guard.alerts.where((a) => !a.active).toList();
        return Scaffold(
          appBar: AppBar(
            title: Text(guard.name ?? ''),
            actions: [IconButton(tooltip: 'Sign out', icon: const Icon(Icons.logout), onPressed: guard.signOut)],
          ),
          body: RefreshIndicator(
            color: p.red,
            onRefresh: () => guard.refresh(announce: false),
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
              children: [
                _DutyCard(guard: guard),
                if (guard.error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 10),
                    child: Text(guard.error!, style: TextStyle(color: p.red, fontSize: 14)),
                  ),
                const _Label('Active'),
                if (active.isEmpty)
                  _Empty(onDuty: guard.onDuty)
                else
                  for (final a in active) _AlertTile(alert: a, onTap: () => _open(a.eventId)),
                if (earlier.isNotEmpty) ...[
                  const _Label('Earlier'),
                  for (final a in earlier) _AlertTile(alert: a, onTap: () => _open(a.eventId)),
                ],
              ],
            ),
          ),
        );
      },
    );
  }
}

class _DutyCard extends StatelessWidget {
  const _DutyCard({required this.guard});
  final Guard guard;

  @override
  Widget build(BuildContext context) {
    final p = Palette.of(context);
    final on = guard.onDuty;
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 18, 12, 18),
      decoration: BoxDecoration(
        color: on ? p.redWash : p.paper,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: on ? p.red : p.line),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(on ? 'On duty' : 'Off duty',
                    style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700, color: on ? p.red : p.ink)),
                const SizedBox(height: 2),
                Text(on ? 'Sharing location · alerts on' : 'Alerts paused',
                    style: TextStyle(fontSize: 14, color: p.muted)),
              ],
            ),
          ),
          Transform.scale(
            scale: 1.15,
            child: Switch.adaptive(
              value: on,
              activeTrackColor: p.red,
              onChanged: guard.busy ? null : guard.setDuty,
            ),
          ),
        ],
      ),
    );
  }
}

class _AlertTile extends StatelessWidget {
  const _AlertTile({required this.alert, required this.onTap});
  final Alert alert;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final p = Palette.of(context);
    final a = alert;
    final (status, statusColor) = a.mine
        ? ('You are responding', p.red)
        : a.takenByOther
            ? ('Taken by ${a.claimedByName ?? 'another guard'}', p.muted)
            : a.status == 'declined'
                ? ('Declined', p.muted)
                : ('', p.muted);
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: p.paper,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: a.active ? p.red : p.line, width: a.active ? 1.5 : 1),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 72,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('${a.speed.round()}',
                          style: TextStyle(
                              fontSize: 30,
                              height: 1.05,
                              fontWeight: FontWeight.w700,
                              color: a.active ? p.red : p.muted,
                              fontFeatures: const [FontFeature.tabularFigures()])),
                      Text('km/h', style: TextStyle(fontSize: 12, color: p.muted)),
                    ],
                  ),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('${a.vehicleType} · limit ${a.limit.round()}',
                          style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: p.ink)),
                      const SizedBox(height: 2),
                      Text(a.where, style: TextStyle(fontSize: 14, color: p.muted)),
                      if (status.isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 4),
                          child: Text(status,
                              style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: statusColor)),
                        ),
                    ],
                  ),
                ),
                Text(ago(a.sentAt), style: TextStyle(fontSize: 12, color: p.muted)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _Label extends StatelessWidget {
  const _Label(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 24, 0, 10),
        child: Text(text.toUpperCase(),
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: Palette.of(context).muted, letterSpacing: 1.2)),
      );
}

class _Empty extends StatelessWidget {
  const _Empty({required this.onDuty});
  final bool onDuty;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 40),
        child: Column(
          children: [
            Text('No alerts', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: Palette.of(context).ink)),
            const SizedBox(height: 4),
            Text(
              onDuty ? 'You will be alerted when a vehicle near you is speeding.' : 'Go on duty to receive alerts.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 14, color: Palette.of(context).muted),
            ),
          ],
        ),
      );
}

class _NotAGuard extends StatelessWidget {
  const _NotAGuard({required this.onSignOut});
  final Future<void> Function() onSignOut;

  @override
  Widget build(BuildContext context) => Scaffold(
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('Not a guard account',
                    style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700, color: Palette.of(context).ink)),
                const SizedBox(height: 8),
                Text('This login is not registered for alerts. Ask your supervisor to add you.',
                    style: TextStyle(fontSize: 15, color: Palette.of(context).muted)),
                const SizedBox(height: 24),
                FilledButton(onPressed: onSignOut, child: const Text('SIGN OUT')),
              ],
            ),
          ),
        ),
      );
}
