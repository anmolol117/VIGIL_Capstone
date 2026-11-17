import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'lot.dart';
import 'lot_painter.dart';
import 'reset_password_screen.dart';
import 'theme.dart';

class SignInScreen extends StatefulWidget {
  const SignInScreen({super.key});

  @override
  State<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends State<SignInScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _creatingAccount = false;
  bool _busy = false;
  String? _message;
  bool _messageIsError = true;

  SupabaseClient get _client => Supabase.instance.client;

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
      _messageIsError = isError;
    });
  }

  Future<void> _submit() async {
    final email = _email.text.trim();
    final password = _password.text;
    if (email.isEmpty || password.isEmpty) {
      _show('Enter your email and password.');
      return;
    }
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      if (_creatingAccount) {
        await _createAccount(email, password);
      } else {
        // On success the auth gate swaps to the lots screen by itself.
        await _client.auth.signInWithPassword(email: email, password: password);
      }
    } on AuthException catch (e) {
      _show(_friendly(e));
    } catch (_) {
      _show('Could not reach the server. Check your connection.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // Open to any email. Accounts are shared with Guard Alert, but guard access
  // comes from an admin-created guards row, so a parking sign-up grants nothing there.
  Future<void> _createAccount(String email, String password) async {
    final res = await _client.auth.signUp(email: email, password: password);
    if (res.session == null) {
      if (mounted) setState(() => _creatingAccount = false);
      _show(
        'Check your inbox to confirm your email, then sign in.',
        isError: false,
      );
    }
  }

  String _friendly(AuthException e) => switch (e.code) {
    'invalid_credentials' => 'Wrong email or password.',
    'email_not_confirmed' => 'Confirm your email first. Check your inbox.',
    'user_already_exists' =>
      'That email already has an account. Sign in instead.',
    'over_email_send_rate_limit' =>
      'Too many attempts. Try again in a few minutes.',    _ => e.message,
  };

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
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
                    const _LotStrip(),
                    const SizedBox(height: 32),
                    Text(
                      'Campus Parking',
                      style: theme.textTheme.headlineMedium,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      _creatingAccount
                          ? 'Create an account to see live parking.'
                          : 'Sign in to see live parking availability.',
                      style: theme.textTheme.bodyMedium?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: 24),
                    TextField(
                      controller: _email,
                      enabled: !_busy,
                      keyboardType: TextInputType.emailAddress,
                      autocorrect: false,
                      textInputAction: TextInputAction.next,
                      autofillHints: const [AutofillHints.email],
                      decoration: const InputDecoration(labelText: 'Email'),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _password,
                      enabled: !_busy,
                      obscureText: true,
                      textInputAction: TextInputAction.done,
                      onSubmitted: (_) => _submit(),
                      autofillHints: [
                        _creatingAccount
                            ? AutofillHints.newPassword
                            : AutofillHints.password,
                      ],
                      decoration: const InputDecoration(labelText: 'Password'),
                    ),
                    if (!_creatingAccount)
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
                    if (_message != null) ...[
                      const SizedBox(height: 12),
                      Text(
                        _message!,
                        style: theme.textTheme.bodyMedium?.copyWith(
                          color: _messageIsError
                              ? theme.colorScheme.error
                              : theme.colorScheme.onSurface,
                        ),
                      ),
                    ],
                    const SizedBox(height: 20),
                    FilledButton(
                      onPressed: _busy ? null : _submit,
                      child: _busy
                          ? const SizedBox.square(
                              dimension: 20,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : Text(
                              _creatingAccount ? 'Create account' : 'Sign in',
                            ),
                    ),
                    const SizedBox(height: 8),
                    TextButton(
                      onPressed: _busy
                          ? null
                          : () => setState(() {
                              _creatingAccount = !_creatingAccount;
                              _message = null;
                            }),
                      child: Text(
                        _creatingAccount
                            ? 'Have an account? Sign in'
                            : 'New here? Create an account',
                      ),
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

/// A small stretch of lot, drawn by the same painter as the live map.
class _LotStrip extends StatelessWidget {
  const _LotStrip();

  static final _bays = [
    for (final (row, taken) in ['10110111011', '11011010111'].indexed)
      for (var i = 0; i < taken.length; i++)
        Bay(
          id: '$row-$i',
          code: 'S-$row-$i',
          occupied: taken[i] == '1',
          points: [
            Offset(i * 18.0, row * 36.0),
            Offset(i * 18.0 + 18, row * 36.0),
            Offset(i * 18.0 + 18, row * 36.0 + 36),
            Offset(i * 18.0, row * 36.0 + 36),
          ],
        ),
  ];
  static final _bounds = baysBounds(_bays, margin: 10);

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(24),
      child: ColoredBox(
        color: Palette.asphalt,
        child: AspectRatio(
          aspectRatio: _bounds.width / _bounds.height,
          child: CustomPaint(
            painter: LotPainter(bays: _bays, bounds: _bounds),
          ),
        ),
      ),
    );
  }
}
