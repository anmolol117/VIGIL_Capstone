import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'backend.dart';
import 'home.dart';
import 'login.dart';
import 'push.dart';
import 'ui.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // The publishable key is meant to ship inside apps: what a signed-in guard
  // can read or change is enforced by row-level security on the server.
  await Supabase.initialize(
    url: 'https://grknwhtsoginetxkmeto.supabase.co',
    publishableKey: 'sb_publishable_1xvfFHv79zkbdhOkYTStzA_p5XO-5gf',
  );
  await Push.init();
  runApp(const GuardApp());
}

class GuardApp extends StatelessWidget {
  const GuardApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Guard Alert',
      debugShowCheckedModeBanner: false,
      theme: appTheme,
      darkTheme: darkTheme,
      home: StreamBuilder<AuthState>(
        stream: db.auth.onAuthStateChange,
        builder: (context, _) => db.auth.currentSession == null ? const LoginScreen() : const HomeScreen(),
      ),
    );
  }
}
