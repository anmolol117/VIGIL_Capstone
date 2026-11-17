import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'backend.dart';
import 'reset_password.dart';
import 'ui.dart';

/// Only campus addresses can be guards (public.allowed_email_domains in the
/// database), so sign-up asks for one up front.
bool isCampusEmail(String email) => email.trim().toLowerCase().endsWith('@thapar.edu');

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _creating = false;
  bool _busy = false;
  String? _message;
  bool _isError = true;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  void _show(String message, {bool isError = true}) {
    if (!mounted) return;
    setState(() {
      _message = message;
      _isError = isError;
    });
  }

  Future<void> _submit() async {
    final email = _email.text.trim();
    final password = _password.text;
    if (email.isEmpty || password.isEmpty) return _show('Enter your email and password.');
    if (_creating && !isCampusEmail(email)) return _show('Use your @thapar.edu email.');
    if (_creating && password.length < 8) return _show('Use at least 8 characters for the password.');
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      if (_creating) {
        await _createAccount(email, password);
      } else {
        await db.auth.signInWithPassword(email: email, password: password);
      }
    } on AuthException catch (e) {
      _show(switch (e.code) {
        'invalid_credentials' => 'Wrong email or password.',
        'email_not_confirmed' => 'Confirm your email first. Check your inbox.',
        'user_already_exists' => 'That email already has an account, maybe from Campus Parking. Sign in instead.',
        'over_email_send_rate_limit' => 'Too many attempts. Try again in a few minutes.',
        _ => e.message,
      });
    } catch (_) {
      _show('Could not reach the server. Check your connection.');
    }
    if (mounted) setState(() => _busy = false);
  }

  // The account alone grants nothing: alerts start once a supervisor adds the
  // guard (speed/guards_admin.py add). Until then home shows "Not a guard account".
  Future<void> _createAccount(String email, String password) async {
    final res = await db.auth.signUp(email: email, password: password);
    if (res.session == null) {
      if (mounted) setState(() => _creating = false);
      _show('Check your inbox to confirm your email, then sign in. '
          'Alerts start once your supervisor adds you as a guard.', isError: false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = Palette.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: AutofillGroup(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text('Guard Alert',
                        style: TextStyle(fontSize: 30, fontWeight: FontWeight.w700, color: p.ink)),
                    const SizedBox(height: 4),
                    Text(_creating ? 'Create an account with your campus email' : 'Sign in with your guard account',
                        style: TextStyle(fontSize: 15, color: p.muted)),
                    const SizedBox(height: 32),
                    TextField(
                      controller: _email,
                      enabled: !_busy,
                      keyboardType: TextInputType.emailAddress,
                      autofillHints: const [AutofillHints.email],
                      autocorrect: false,
                      textInputAction: TextInputAction.next,
                      decoration: InputDecoration(labelText: _creating ? 'Email (@thapar.edu)' : 'Email'),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _password,
                      enabled: !_busy,
                      obscureText: true,
                      autofillHints: [_creating ? AutofillHints.newPassword : AutofillHints.password],
                      onSubmitted: (_) => _busy ? null : _submit(),
                      decoration: const InputDecoration(labelText: 'Password'),
                    ),
                    if (!_creating)
                      Align(
                        alignment: Alignment.centerRight,
                        child: TextButton(
                          onPressed: _busy
                              ? null
                              : () => Navigator.of(context).push(MaterialPageRoute(
                                  builder: (_) => ResetPasswordScreen(email: _email.text.trim()))),
                          child: const Text('Forgot password?'),
                        ),
                      ),
                    if (_message != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 12),
                        child: Text(_message!, style: TextStyle(color: _isError ? p.red : p.ink, fontSize: 14)),
                      ),
                    const SizedBox(height: 24),
                    FilledButton(
                      onPressed: _busy ? null : _submit,
                      child: _busy
                          ? SizedBox(
                              width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5, color: p.bg))
                          : Text(_creating ? 'CREATE ACCOUNT' : 'SIGN IN'),
                    ),
                    const SizedBox(height: 8),
                    TextButton(
                      onPressed: _busy ? null : () => setState(() {
                                _creating = !_creating;
                                _message = null;
                              }),
                      child: Text(_creating ? 'Have an account? Sign in' : 'New guard? Create an account'),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
