import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'lots_screen.dart';
import 'sign_in_screen.dart';
import 'theme.dart';

// Supplied at build time: flutter run --dart-define-from-file=env.json
// The publishable key is meant to ship inside the app; Row Level Security in the
// database is what limits a signed-in user to reading lot occupancy.
const _supabaseUrl = String.fromEnvironment('SUPABASE_URL');
const _publishableKey = String.fromEnvironment('SUPABASE_PUBLISHABLE_KEY');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (_supabaseUrl.isEmpty || _publishableKey.isEmpty) {
    runApp(const _MissingConfig());
    return;
  }
  await Supabase.initialize(url: _supabaseUrl, publishableKey: _publishableKey);
  runApp(const CampusParkingApp());
}

class CampusParkingApp extends StatelessWidget {
  const CampusParkingApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Campus Parking',
      debugShowCheckedModeBanner: false,
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      home: const _AuthGate(),
    );
  }
}

/// Shows the lots when signed in, the sign-in screen otherwise. Sessions persist
/// across launches and refresh automatically, so users stay signed in.
class _AuthGate extends StatelessWidget {
  const _AuthGate();

  @override
  Widget build(BuildContext context) {
    final auth = Supabase.instance.client.auth;
    return StreamBuilder<AuthState>(
      stream: auth.onAuthStateChange,
      builder: (context, _) => auth.currentSession == null
          ? const SignInScreen()
          : const LotsScreen(),
    );
  }
}

/// Running without --dart-define-from-file is an easy mistake; say so plainly
/// instead of crashing on an empty URL.
class _MissingConfig extends StatelessWidget {
  const _MissingConfig();

  @override
  Widget build(BuildContext context) {
    return const MaterialApp(
      home: Scaffold(
        body: Center(
          child: Padding(
            padding: EdgeInsets.all(24),
            child: Text(
              'Missing configuration.\n\nRun with:\n'
              'flutter run --dart-define-from-file=env.json',
              textAlign: TextAlign.center,
            ),
          ),
        ),
      ),
    );
  }
}
