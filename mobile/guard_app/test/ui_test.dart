import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guard_app/login.dart';
import 'package:guard_app/ui.dart';

void main() {
  testWidgets('palette follows the phone appearance', (tester) async {
    Palette? seen;
    final app = MaterialApp(
      theme: appTheme,
      darkTheme: darkTheme,
      home: Builder(builder: (context) {
        seen = Palette.of(context);
        return const SizedBox();
      }),
    );
    await tester.pumpWidget(app);
    expect(seen, Palette.light);

    tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;
    addTearDown(tester.platformDispatcher.clearPlatformBrightnessTestValue);
    await tester.pumpAndSettle();
    expect(seen, Palette.dark);
  });

  testWidgets('sign-up refuses a non-campus email; forgot password opens the reset screen', (tester) async {
    await tester.pumpWidget(MaterialApp(theme: appTheme, home: const LoginScreen()));
    await tester.tap(find.text('New guard? Create an account'));
    await tester.pump();
    await tester.enterText(find.widgetWithText(TextField, 'Email (@thapar.edu)'), 'ravi@gmail.com');
    await tester.enterText(find.widgetWithText(TextField, 'Password'), 'longenough');
    await tester.tap(find.text('CREATE ACCOUNT'));
    await tester.pump();
    expect(find.text('Use your @thapar.edu email.'), findsOneWidget);   // stopped before any network call

    await tester.tap(find.text('Have an account? Sign in'));
    await tester.pump();
    await tester.tap(find.text('Forgot password?'));
    await tester.pumpAndSettle();
    expect(find.text('Reset password'), findsOneWidget);
    expect(find.text('SEND CODE'), findsOneWidget);
  });

  test('guard sign-up takes campus emails only', () {
    expect(isCampusEmail('ravi@thapar.edu'), isTrue);
    expect(isCampusEmail(' Ravi@Thapar.EDU '), isTrue);
    expect(isCampusEmail('ravi@gmail.com'), isFalse);
    expect(isCampusEmail('ravi@thapar.edu.evil.com'), isFalse);
  });

  test('compass direction from bearing', () {
    expect(direction(0), 'N');
    expect(direction(44), 'NE');
    expect(direction(90), 'E');
    expect(direction(180), 'S');
    expect(direction(270), 'W');
    expect(direction(337), 'NW');
    expect(direction(359), 'N'); // wraps, not index 8
  });

  test('distance text', () {
    expect(distance(0), '0 m');
    expect(distance(347), '350 m');
    expect(distance(999), '1.0 km');
    expect(distance(1000), '1.0 km');
    expect(distance(2449), '2.4 km');
  });
}
