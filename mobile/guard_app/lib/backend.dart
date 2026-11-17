import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:geolocator/geolocator.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'ui.dart';

SupabaseClient get db => Supabase.instance.client;

/// One alert as a guard sees it: their dispatch row joined with the event.
class Alert {
  Alert.fromRow(Map<String, dynamic> r)
      : dispatchId = r['id'] as int,
        eventId = r['event_id'] as int,
        distanceM = (r['distance_m'] as num).toDouble(),
        bearingDeg = (r['bearing_deg'] as num).toDouble(),
        status = r['status'] as String,
        sentAt = DateTime.parse(r['sent_at'] as String),
        detectedAt = DateTime.parse(r['speeding_events']['detected_at'] as String),
        camera = r['speeding_events']['camera'] as String,
        vehicleType = r['speeding_events']['vehicle_type'] as String,
        speed = (r['speeding_events']['speed_kmh'] as num).toDouble(),
        limit = (r['speeding_events']['speed_limit'] as num).toDouble(),
        frameUrl = r['speeding_events']['frame_url'] as String?,
        lat = (r['speeding_events']['lat'] as num?)?.toDouble(),
        lon = (r['speeding_events']['lon'] as num?)?.toDouble(),
        claimedBy = r['speeding_events']['claimed_by'] as String?,
        claimedByName = r['speeding_events']['claimed_by_name'] as String?;

  final int dispatchId, eventId;
  final double distanceM, bearingDeg, speed, limit;
  final String status, camera, vehicleType;
  final DateTime sentAt, detectedAt;
  final String? frameUrl, claimedBy, claimedByName;
  final double? lat, lon;

  bool get mine => claimedBy != null && claimedBy == db.auth.currentUser?.id;
  bool get takenByOther => claimedBy != null && !mine;

  /// Still needs attention: recent, not declined, and not taken by someone else.
  bool get active =>
      status != 'declined' && !takenByOther && DateTime.now().difference(sentAt).inMinutes < 15;

  String get where => '${distance(distanceM)} ${direction(bearingDeg)} · $camera';
  String get summary => '$vehicleType · limit ${limit.round()} · $where';
}

/// The signed-in guard: duty state, location sharing and live alerts.
class Guard extends ChangeNotifier {
  String? name;
  bool registered = true;
  bool onDuty = false;
  bool busy = false;
  String? error;
  List<Alert> alerts = [];

  /// Called once for each alert that arrives while the app is open.
  void Function(Alert)? onNewAlert;

  final _seen = <int>{};
  RealtimeChannel? _channel, _alerts;
  StreamSubscription<Position>? _positions;
  Timer? _heartbeat;
  Position? _last;
  DateTime _lastFix = DateTime.fromMillisecondsSinceEpoch(0);

  String get _uid => db.auth.currentUser!.id;

  Future<void> start() async {
    final row = await db.from('guards').select('name, on_duty').eq('id', _uid).maybeSingle();
    if (row == null) {
      // Logins are shared with the campus parking app; only listed guards get alerts.
      registered = false;
      notifyListeners();
      return;
    }
    name = row['name'] as String;
    onDuty = row['on_duty'] as bool;
    await refresh(announce: false);

    _channel = db
        .channel('guard-$_uid')
        .onPostgresChanges(
          event: PostgresChangeEvent.insert,
          schema: 'public',
          table: 'dispatches',
          filter: PostgresChangeFilter(type: PostgresChangeFilterType.eq, column: 'guard_id', value: _uid),
          callback: (p) {
            // Stamp arrival before any query, so the receipt measures delivery alone.
            final id = p.newRecord['id'];
            if (id is int) _stamp(id, 'delivered_at', DateTime.now());
            refresh();
          },
        )
        // Claims and late-arriving evidence images.
        .onPostgresChanges(
          event: PostgresChangeEvent.update,
          schema: 'public',
          table: 'speeding_events',
          callback: (_) => refresh(announce: false),
        )
        .subscribe();

    // Fast path: the dispatch trigger broadcasts straight to this guard's
    // private channel. The postgres_changes insert above is the fallback.
    _alerts = db
        .channel('guard:$_uid', opts: const RealtimeChannelConfig(private: true))
        .onBroadcast(
          event: 'alert',
          callback: (msg) {
            final arrived = DateTime.now();
            final data = msg['payload'] is Map ? msg['payload'] as Map : msg;
            final id = data['dispatch_id'];
            if (id is int) _stamp(id, 'delivered_at', arrived);
            refresh();
          },
        )
        .subscribe();

    if (onDuty) await _track();
    notifyListeners();
  }

  Future<void> refresh({bool announce = true}) async {
    try {
      final rows = await db
          .from('dispatches')
          .select('id, event_id, distance_m, bearing_deg, status, sent_at, '
              'speeding_events(detected_at, camera, vehicle_type, speed_kmh, speed_limit, '
              'frame_url, lat, lon, claimed_by, claimed_by_name)')
          .order('sent_at', ascending: false)
          .limit(50);
      alerts = rows.map(Alert.fromRow).toList();
      for (final a in alerts) {
        if (_seen.add(a.eventId) && announce && a.active) onNewAlert?.call(a);
      }
      notifyListeners();
    } catch (e) {
      debugPrint('refresh failed: $e');
    }
  }

  Alert? byEvent(int eventId) => alerts.where((a) => a.eventId == eventId).firstOrNull;

  /// A push arrived or was tapped. Returns the alert, and whether the app had
  /// not shown it yet -- Realtime usually gets there first while it is open.
  Future<(Alert?, bool)> fromPush(int eventId) async {
    final arrived = DateTime.now();
    final isNew = !_seen.contains(eventId);
    if (byEvent(eventId) == null) await refresh(announce: false);
    _seen.add(eventId);
    final a = byEvent(eventId);
    if (a != null && isNew) _stamp(a.dispatchId, 'delivered_at', arrived);
    return (a, isNew);
  }

  /// Receipts feed latency measurement. Client clock; phones keep NTP time.
  Future<void> _stamp(int dispatchId, String column, DateTime at) async {
    try {
      await db
          .from('dispatches')
          .update({column: at.toUtc().toIso8601String()})
          .eq('id', dispatchId)
          .isFilter(column, null);
    } catch (_) {}
  }

  void opened(Alert a) => _stamp(a.dispatchId, 'opened_at', DateTime.now());

  /// 'responding', 'declined', or `taken:NAME`.
  Future<String> respond(Alert a, String action) async {
    final result = await db.rpc('respond_to_event', params: {'p_event_id': a.eventId, 'p_action': action});
    await refresh(announce: false);
    return result as String;
  }

  Future<void> setDuty(bool on) async {
    busy = true;
    error = null;
    notifyListeners();
    try {
      if (on) {
        if (!await _locationAllowed()) {
          error = 'Location access is needed to receive nearby alerts.';
          return;
        }
        final p = await Geolocator.getCurrentPosition();
        _last = p;
        await db.from('guards').update({'on_duty': true, ..._fix(p)}).eq('id', _uid);
        _lastFix = DateTime.now();
        onDuty = true;
        await _track();
      } else {
        await _positions?.cancel();
        _positions = null;
        _heartbeat?.cancel();
        // Off duty: stop sharing and forget the last position.
        await db.from('guards').update({'on_duty': false, 'lat': null, 'lon': null, 'location_at': null}).eq('id', _uid);
        onDuty = false;
      }
    } catch (e) {
      error = 'Could not change duty status. Check your connection.';
      debugPrint('setDuty: $e');
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<bool> _locationAllowed() async {
    if (!await Geolocator.isLocationServiceEnabled()) return false;
    var p = await Geolocator.checkPermission();
    if (p == LocationPermission.denied) p = await Geolocator.requestPermission();
    return p == LocationPermission.whileInUse || p == LocationPermission.always;
  }

  Map<String, dynamic> _fix(Position p) =>
      {'lat': p.latitude, 'lon': p.longitude, 'location_at': DateTime.now().toUtc().toIso8601String()};

  /// Continuous updates, written at most every 15 s. Started while the app is
  /// visible, so "while in use" permission is enough to keep running in the
  /// background: Android via a foreground-service notification, iOS via the
  /// blue location indicator.
  Future<void> _track() async {
    await _positions?.cancel();
    // A phone that isn't moving may report no new positions, and the server
    // skips guards whose location is over 30 min old. Re-stamp the last fix so
    // a stationary guard on duty stays dispatchable.
    _heartbeat?.cancel();
    _heartbeat = Timer.periodic(const Duration(minutes: 5), (_) async {
      final p = _last;
      if (p == null) return;
      try {
        await db.from('guards').update(_fix(p)).eq('id', _uid);
      } catch (_) {}
    });
    final settings = Platform.isAndroid
        ? AndroidSettings(
            accuracy: LocationAccuracy.high,
            intervalDuration: const Duration(seconds: 10),
            foregroundNotificationConfig: const ForegroundNotificationConfig(
              notificationTitle: 'On duty',
              notificationText: 'Sharing your location so you get nearby alerts',
              enableWakeLock: true,
            ),
          )
        : AppleSettings(
            accuracy: LocationAccuracy.high,
            activityType: ActivityType.other,
            pauseLocationUpdatesAutomatically: false,
            showBackgroundLocationIndicator: true,
            allowBackgroundLocationUpdates: true,
          );
    _positions = Geolocator.getPositionStream(locationSettings: settings).listen(
      (p) async {
        _last = p;
        if (DateTime.now().difference(_lastFix).inSeconds < 15) return;
        _lastFix = DateTime.now();
        try {
          await db.from('guards').update(_fix(p)).eq('id', _uid);
        } catch (_) {} // next fix retries
      },
      onError: (e) {
        error = 'Location stopped. Turn location services back on.';
        notifyListeners();
      },
    );
  }

  Future<void> signOut() async {
    if (onDuty) await setDuty(false); // never stay dispatchable without a running app
    await _positions?.cancel();
    _heartbeat?.cancel();
    for (final c in [_channel, _alerts]) {
      if (c != null) await db.removeChannel(c);
    }
    await db.auth.signOut();
  }

  @override
  void dispose() {
    _positions?.cancel();
    _heartbeat?.cancel();
    for (final c in [_channel, _alerts]) {
      if (c != null) db.removeChannel(c);
    }
    super.dispose();
  }
}
