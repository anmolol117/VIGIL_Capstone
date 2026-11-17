import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import 'backend.dart';
import 'ui.dart';

class AlertScreen extends StatefulWidget {
  const AlertScreen({super.key, required this.guard, required this.eventId});
  final Guard guard;
  final int eventId;

  @override
  State<AlertScreen> createState() => _AlertScreenState();
}

class _AlertScreenState extends State<AlertScreen> {
  Timer? _tick;
  bool _sending = false;
  String? _note;

  @override
  void initState() {
    super.initState();
    final a = widget.guard.byEvent(widget.eventId);
    if (a != null) widget.guard.opened(a);
    _tick = Timer.periodic(const Duration(seconds: 1), (_) => setState(() {}));
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  Future<void> _respond(Alert a, String action) async {
    setState(() {
      _sending = true;
      _note = null;
    });
    try {
      final result = await widget.guard.respond(a, action);
      if (result.startsWith('taken:')) {
        _note = 'Already taken by ${result.substring(6)}.';
      } else if (result == 'declined' && mounted) {
        Navigator.of(context).pop();
        return;
      }
    } catch (_) {
      _note = 'Could not send. Check your connection and try again.';
    }
    if (mounted) setState(() => _sending = false);
  }

  Future<void> _navigate(Alert a) => launchUrl(
        Uri.parse(Platform.isIOS
            ? 'https://maps.apple.com/?daddr=${a.lat},${a.lon}'
            : 'https://www.google.com/maps/dir/?api=1&destination=${a.lat},${a.lon}'),
        mode: LaunchMode.externalApplication,
      );

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: widget.guard,
      builder: (context, _) {
        final p = Palette.of(context);
        final a = widget.guard.byEvent(widget.eventId);
        if (a == null) {
          return Scaffold(appBar: AppBar(), body: Center(child: CircularProgressIndicator(color: p.red)));
        }
        return Scaffold(
          appBar: AppBar(
            title: Text('OVERSPEED',
                style: TextStyle(color: p.red, fontSize: 14, fontWeight: FontWeight.w700, letterSpacing: 1.6)),
            actions: [
              Padding(
                padding: const EdgeInsets.only(right: 16),
                child: Center(child: Text(ago(a.detectedAt), style: TextStyle(color: p.muted))),
              ),
            ],
          ),
          body: SafeArea(
            child: Column(
              children: [
                Expanded(
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(20, 8, 20, 12),
                    children: [
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.baseline,
                        textBaseline: TextBaseline.alphabetic,
                        children: [
                          Text('${a.speed.round()}',
                              style: TextStyle(
                                  fontSize: 76, height: 1, fontWeight: FontWeight.w700, color: p.red)),
                          const SizedBox(width: 6),
                          Text('km/h',
                              style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600, color: p.red)),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Text('limit ${a.limit.round()} · ${a.vehicleType}',
                          style: TextStyle(fontSize: 18, color: p.ink)),
                      const SizedBox(height: 20),
                      const Divider(height: 1),
                      const SizedBox(height: 20),
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.baseline,
                        textBaseline: TextBaseline.alphabetic,
                        children: [
                          Text(distance(a.distanceM),
                              style: TextStyle(fontSize: 30, fontWeight: FontWeight.w700, color: p.ink)),
                          const SizedBox(width: 10),
                          Text(direction(a.bearingDeg),
                              style: TextStyle(fontSize: 30, fontWeight: FontWeight.w700, color: p.muted)),
                        ],
                      ),
                      const SizedBox(height: 2),
                      Text('from you · ${a.camera}', style: TextStyle(fontSize: 15, color: p.muted)),
                      const SizedBox(height: 20),
                      ClipRRect(
                        borderRadius: BorderRadius.circular(12),
                        child: AspectRatio(
                          aspectRatio: 4 / 3,
                          // Contain, not cover: the evidence crop carries its caption
                          // (speed, limit, time) along the bottom edge.
                          child: a.frameUrl == null
                              ? const _ImagePlaceholder('Evidence image loading…')
                              : Container(
                                  color: p.paper,
                                  child: Image.network(a.frameUrl!,
                                      fit: BoxFit.contain,
                                      errorBuilder: (context, error, stack) =>
                                          const _ImagePlaceholder('Evidence image unavailable')),
                                ),
                        ),
                      ),
                    ],
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 4, 20, 16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      if (a.mine)
                        _Banner('You are responding', p.red)
                      else if (a.takenByOther)
                        _Banner('Taken by ${a.claimedByName ?? 'another guard'}', p.muted)
                      else if (a.status == 'declined')
                        _Banner('You declined this alert', p.muted)
                      else
                        FilledButton(
                          // the one red button: this action answers an alert
                          style: FilledButton.styleFrom(backgroundColor: p.red, foregroundColor: Colors.white),
                          onPressed: _sending ? null : () => _respond(a, 'responding'),
                          child: const Text('RESPONDING'),
                        ),
                      if (_note != null)
                        Padding(
                          padding: const EdgeInsets.only(top: 8),
                          child: Text(_note!, textAlign: TextAlign.center, style: TextStyle(color: p.red)),
                        ),
                      const SizedBox(height: 6),
                      Row(
                        children: [
                          Expanded(
                            child: TextButton(
                              onPressed: a.lat == null ? null : () => _navigate(a),
                              child: const Text('Navigate', style: TextStyle(fontSize: 16)),
                            ),
                          ),
                          if (a.status == 'sent' && !a.takenByOther)
                            Expanded(
                              child: TextButton(
                                onPressed: _sending ? null : () => _respond(a, 'declined'),
                                child: Text("Can't respond", style: TextStyle(fontSize: 16, color: p.muted)),
                              ),
                            ),
                        ],
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner(this.text, this.color);
  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final p = Palette.of(context);
    return Container(
      height: 56,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: color == p.red ? p.redWash : p.paper,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color == p.red ? p.red : p.line),
      ),
      child: Text(text, style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: color)),
    );
  }
}

class _ImagePlaceholder extends StatelessWidget {
  const _ImagePlaceholder(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Container(
        color: Palette.of(context).paper,
        alignment: Alignment.center,
        child: Text(text, style: TextStyle(color: Palette.of(context).muted)),
      );
}
