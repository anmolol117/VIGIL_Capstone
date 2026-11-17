import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

/// Forgot password: a code is emailed, then the code plus a new password reset
/// it and sign the user in. Codes work wherever the email is read; no deep link.
/// Needs {{ .Token }} in the project's "Reset Password" email template.
/// Pushed over the sign-in screen, so it survives the auth gate swapping to the
/// lots underneath once the code signs the user in.
class ResetPasswordScreen extends StatefulWidget {
  const ResetPasswordScreen({super.key, this.email = ''});

  final String email;

  @override
  State<ResetPasswordScreen> createState() => _ResetPasswordScreenState();
}

class _ResetPasswordScreenState extends State<ResetPasswordScreen> {
  late final _email = TextEditingController(text: widget.email);
  final _code = TextEditingController();
  final _password = TextEditingController();
  bool _codeSent = false;
  bool _busy = false;
  String? _message;
  bool _messageIsError = true;

  GoTrueClient get _auth => Supabase.instance.client.auth;

  @override
  void dispose() {
    _email.dispose();
    _code.dispose();
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

  Future<void> _run(Future<void> Function() action) async {
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await action();
    } on AuthException catch (e) {
      _show(_friendly(e));
    } catch (_) {
      _show('Could not reach the server. Check your connection.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _sendCode() async {
    final email = _email.text.trim();
    if (!email.contains('@')) return _show('Enter the email you signed up with.');
    await _run(() async {
      // Succeeds for unknown emails too, so it never reveals who has an account.
      await _auth.resetPasswordForEmail(email);
      if (mounted) setState(() => _codeSent = true);
      _show('If $email has an account, a code is on its way. It can take a minute.', isError: false);
    });
  }

  Future<void> _reset() async {
    final code = _code.text.trim();
    final password = _password.text;
    if (code.length < 6) return _show('Enter the code from the email.');
    if (password.length < 8) return _show('Use at least 8 characters for the new password.');
    await _run(() async {
      // A retry after a rejected password is already signed in by the code.
      if (_auth.currentSession == null) {
        await _auth.verifyOTP(email: _email.text.trim(), token: code, type: OtpType.recovery);
      }
      await _auth.updateUser(UserAttributes(password: password));
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Password changed.')));
      Navigator.of(context).pop();
    });
  }

  String _friendly(AuthException e) => switch (e.code) {
    'otp_expired' => 'That code is wrong or has expired. Send a new one.',
    'same_password' => 'Choose a password you have not used here before.',
    'over_email_send_rate_limit' => 'Too many emails. Try again in a few minutes.',
    _ => e.message,
  };

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Reset password')),
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
                    Text(
                      _codeSent
                          ? 'Enter the code from the email and choose a new password.'
                          : 'We will email you a code to reset your password.',
                      style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                    ),
                    const SizedBox(height: 24),
                    TextField(
                      controller: _email,
                      enabled: !_busy && !_codeSent,
                      keyboardType: TextInputType.emailAddress,
                      autocorrect: false,
                      autofillHints: const [AutofillHints.email],
                      textInputAction: TextInputAction.done,
                      onSubmitted: (_) => _codeSent ? null : _sendCode(),
                      decoration: const InputDecoration(labelText: 'Email'),
                    ),
                    if (_codeSent) ...[
                      const SizedBox(height: 12),
                      TextField(
                        controller: _code,
                        enabled: !_busy,
                        keyboardType: TextInputType.number,
                        autofillHints: const [AutofillHints.oneTimeCode],
                        textInputAction: TextInputAction.next,
                        decoration: const InputDecoration(labelText: 'Code'),
                      ),
                      const SizedBox(height: 12),
                      TextField(
                        controller: _password,
                        enabled: !_busy,
                        obscureText: true,
                        autofillHints: const [AutofillHints.newPassword],
                        textInputAction: TextInputAction.done,
                        onSubmitted: (_) => _reset(),
                        decoration: const InputDecoration(labelText: 'New password'),
                      ),
                    ],
                    if (_message != null) ...[
                      const SizedBox(height: 12),
                      Text(
                        _message!,
                        style: theme.textTheme.bodyMedium?.copyWith(
                          color: _messageIsError ? theme.colorScheme.error : theme.colorScheme.onSurface,
                        ),
                      ),
                    ],
                    const SizedBox(height: 20),
                    FilledButton(
                      onPressed: _busy ? null : (_codeSent ? _reset : _sendCode),
                      child: _busy
                          ? const SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 2))
                          : Text(_codeSent ? 'Set new password' : 'Send code'),
                    ),
                    if (_codeSent) ...[
                      const SizedBox(height: 8),
                      TextButton(
                        onPressed: _busy ? null : _sendCode,
                        child: const Text('Send a new code'),
                      ),
                    ],
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
