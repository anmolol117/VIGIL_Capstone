import 'dart:io';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import 'backend.dart';

/// Push (closed app) and the alert sound (open app).
///
/// Works without Firebase configured: push is then off, and alerts still reach
/// the open app instantly over Realtime.
class Push {
  static final _local = FlutterLocalNotificationsPlugin();
  static const _channel = AndroidNotificationChannel(
    'overspeed', // must match channel_id in the dispatch edge function
    'Overspeed alerts',
    description: 'Speeding vehicles near you',
    importance: Importance.max,
  );
  static const _details = NotificationDetails(
    android: AndroidNotificationDetails('overspeed', 'Overspeed alerts',
        importance: Importance.max, priority: Priority.high, category: AndroidNotificationCategory.alarm),
    iOS: DarwinNotificationDetails(
        presentAlert: true, presentSound: true, interruptionLevel: InterruptionLevel.timeSensitive),
  );

  static bool enabled = false;
  static void Function(int eventId)? onOpen;

  static Future<void> init() async {
    await _local.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
        // Permission is asked after sign-in, not before the first screen draws.
        iOS: DarwinInitializationSettings(
            requestAlertPermission: false, requestSoundPermission: false, requestBadgePermission: false),
      ),
      onDidReceiveNotificationResponse: (r) {
        final id = int.tryParse(r.payload ?? '');
        if (id != null) onOpen?.call(id);
      },
    );
    await _local
        .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
        ?.createNotificationChannel(_channel);

    try {
      await Firebase.initializeApp();
      enabled = true;
    } catch (e) {
      debugPrint('push disabled (Firebase not configured): $e');
    }
  }

  /// After sign-in: store this device's token and route incoming pushes.
  static Future<void> register(Future<void> Function(int eventId, bool tapped) onEvent) async {
    // Needed even without Firebase, for the in-app alert sound.
    await _local
        .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission();
    await _local
        .resolvePlatformSpecificImplementation<IOSFlutterLocalNotificationsPlugin>()
        ?.requestPermissions(alert: true, sound: true);
    if (!enabled) return;
    final fm = FirebaseMessaging.instance;
    await fm.requestPermission(alert: true, sound: true, badge: false);
    // The open app plays its own alert; don't double up with the system banner.
    await fm.setForegroundNotificationPresentationOptions(alert: false, badge: false, sound: false);
    try {
      if (Platform.isIOS) await fm.getAPNSToken(); // FCM token needs the APNs token first
      final token = await fm.getToken();
      if (token != null) await _save(token);
    } catch (e) {
      debugPrint('no push token yet: $e');
    }
    fm.onTokenRefresh.listen(_save);

    int? id(RemoteMessage m) => int.tryParse(m.data['event_id'] ?? '');
    FirebaseMessaging.onMessage.listen((m) {
      final e = id(m);
      if (e != null) onEvent(e, false);
    });
    FirebaseMessaging.onMessageOpenedApp.listen((m) {
      final e = id(m);
      if (e != null) onEvent(e, true);
    });
    final initial = await fm.getInitialMessage();
    final e = initial == null ? null : id(initial);
    if (e != null) onEvent(e, true);
  }

  static Future<void> _save(String token) => db
      .from('guards')
      .update({'fcm_token': token, 'platform': Platform.isIOS ? 'ios' : 'android'})
      .eq('id', db.auth.currentUser!.id);

  /// Sound and banner for an alert that arrived while the app is open.
  static Future<void> ring(Alert a) => _local.show(
        id: a.eventId,
        title: 'Overspeed · ${a.speed.round()} km/h',
        body: a.summary,
        notificationDetails: _details,
        payload: '${a.eventId}',
      );
}
