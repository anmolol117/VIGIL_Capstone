import 'package:campus_parking/sign_in_screen.dart';
import 'package:campus_parking/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('forgot password opens the reset screen with the email carried over', (tester) async {
    await tester.pumpWidget(MaterialApp(theme: buildTheme(Brightness.light), home: const SignInScreen()));
    await tester.enterText(find.widgetWithText(TextField, 'Email'), 'driver@example.com');
    await tester.tap(find.text('Forgot password?'));
    await tester.pumpAndSettle();

    expect(find.text('Reset password'), findsOneWidget);
    expect(find.text('driver@example.com'), findsOneWidget);
    expect(find.text('Send code'), findsOneWidget);
  });

  testWidgets('no forgot-password link while creating an account', (tester) async {
    await tester.pumpWidget(MaterialApp(theme: buildTheme(Brightness.light), home: const SignInScreen()));
    await tester.ensureVisible(find.text('New here? Create an account'));
    await tester.tap(find.text('New here? Create an account'));
    await tester.pump();
    expect(find.text('Create account'), findsOneWidget);
    expect(find.text('Forgot password?'), findsNothing);
  });
}
