import 'dart:ui';

import 'package:campus_parking/lot.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('parses a live lot_occupancy row', () {
    final lot = Lot.fromJson({
      'lot_id': '0e0f2b6a-0000-4000-8000-000000000001',
      'code': 'PARKING-A',
      'name': 'Parking A',
      'occupied': 75,
      'free': 90,
      'capacity': 165,
      'pct_full': 45,
      'status': 'plenty',
      'updated_at': '2026-10-08T05:43:49.151+00:00',
    });
    expect(lot.name, 'Parking A');
    expect(
      (lot.occupied, lot.capacity, lot.free, lot.pctFull),
      (75, 165, 90, 45),
    );
    expect(lot.isLive, isTrue);
    expect(lot.hasData, isTrue);
    expect(lot.updatedAt, DateTime.utc(2026, 10, 8, 5, 43, 49, 151));
  });

  test(
    'a lot that has never reported parses with no numbers and is not live',
    () {
      final lot = Lot.fromJson({
        'lot_id': '0e0f2b6a-0000-4000-8000-000000000002',
        'name': 'Parking B',
        'occupied': null,
        'free': null,
        'capacity': null,
        'pct_full': null,
        'status': 'stale',
        'updated_at': null,
      });
      expect(lot.isLive, isFalse);
      expect(lot.hasData, isFalse);
      expect(lot.updatedAt, isNull);
    },
  );

  test('every status the view can return has its own label', () {
    final labels = {
      for (final s in [
        'wide_open',
        'plenty',
        'filling_up',
        'almost_full',
        'full',
        'stale',
      ])
        s: statusStyle(s).label,
    };
    expect(labels, {
      'wide_open': 'Wide open',
      'plenty': 'Plenty of space',
      'filling_up': 'Filling up',
      'almost_full': 'Almost full',
      'full': 'Full',
      'stale': 'No live data',
    });
    // A status added to the view later must not crash older app versions.
    expect(statusStyle('something_new').label, 'No live data');
  });

  test('free-space label is singular for exactly one bay', () {
    expect(freeLabel(1), '1 space free');
    expect(freeLabel(0), '0 spaces free');
    expect(freeLabel(90), '90 spaces free');
  });

  test('updated-ago text, including a server clock ahead of the device', () {
    final now = DateTime.utc(2026, 10, 8, 12);
    expect(updatedAgo(null, now), 'No data yet');
    expect(
      updatedAgo(now.add(const Duration(seconds: 3)), now),
      'Updated just now',
    );
    expect(
      updatedAgo(now.subtract(const Duration(seconds: 2)), now),
      'Updated just now',
    );
    expect(
      updatedAgo(now.subtract(const Duration(seconds: 12)), now),
      'Updated 12s ago',
    );
    expect(
      updatedAgo(now.subtract(const Duration(minutes: 3)), now),
      'Updated 3m ago',
    );
    expect(
      updatedAgo(now.subtract(const Duration(hours: 2)), now),
      'Updated 2h ago',
    );
    expect(
      updatedAgo(now.subtract(const Duration(days: 2)), now),
      'Updated 2d ago',
    );
  });

  Map<String, dynamic> bayJson(Object? state) => {
    'id': 'b1',
    'code': 'PARKING-A-121',
    'polygon': [
      [119, 297.4],
      [137.4, 297.4],
      [137.4, 333],
      [119, 333],
    ],
    'slot_status': state,
  };

  test('bay state parses whether embedded as object, list, or missing', () {
    expect(Bay.fromJson(bayJson({'is_occupied': true})).occupied, isTrue);
    expect(
      Bay.fromJson(
        bayJson([
          {'is_occupied': true},
        ]),
      ).occupied,
      isTrue,
    );
    expect(Bay.fromJson(bayJson(<Object>[])).occupied, isFalse);
    expect(
      Bay.fromJson(bayJson(null)).occupied,
      isFalse,
      reason: 'no state row yet means never seen taken',
    );
  });

  test('bay number, and tapping inside vs outside the real bay outline', () {
    final bay = Bay.fromJson(bayJson({'is_occupied': false}));
    expect(bay.number, '121');
    expect(bay.points.first, const Offset(119, 297.4));
    expect(bay.path.contains(const Offset(128, 315)), isTrue);
    expect(bay.path.contains(const Offset(150, 315)), isFalse);
  });

  test('map bounds cover every bay plus a margin', () {
    final a = Bay.fromJson(bayJson(null));
    final b = Bay.fromJson({
      ...bayJson(null),
      'id': 'b2',
      'polygon': [
        [600, 60],
        [620, 60],
        [620, 100],
        [600, 100],
      ],
    });
    expect(
      baysBounds([a, b], margin: 6),
      const Rect.fromLTRB(113, 54, 626, 339),
    );
    expect(baysBounds(const []), Rect.zero);
  });

  test('cars park nose-in toward the bay they back onto', () {
    Bay at(String id, double top) => Bay(
      id: id,
      code: 'X-$id',
      occupied: true,
      points: [
        Offset(0, top),
        Offset(10, top),
        Offset(10, top + 20),
        Offset(0, top + 20),
      ],
    );
    final upper = at('1', 0), lower = at('2', 20), lone = at('3', 100);
    final bays = [upper, lower, lone];
    double dy(Bay b) => Offset.fromDirection(carHeading(b, bays)).dy;
    expect(dy(upper), closeTo(1, 1e-9), reason: 'faces down into bay 2');
    expect(dy(lower), closeTo(-1, 1e-9), reason: 'faces up into bay 1');
    expect(upper.frame.length, 20);
    expect(upper.frame.width, 10);
    expect(upper.frame.centre, const Offset(5, 10));
  });

  test('the map uses the top-down outline when an angled camera stored one', () {
    final perspective = Bay.fromJson(bayJson(null));
    expect(perspective.points.first, const Offset(119, 297.4));
    final topDown = Bay.fromJson({
      ...bayJson(null),
      'map_polygon': [
        [0, 0],
        [50, 0],
        [50, 100],
        [0, 100],
      ],
    });
    expect(topDown.points.first, Offset.zero);
    expect(topDown.frame.length, 100);
  });
}
