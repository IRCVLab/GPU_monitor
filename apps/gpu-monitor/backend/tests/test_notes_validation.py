from datetime import datetime, timedelta, timezone
import unittest

from pydantic import ValidationError

from backend.routers.notes import NoteCreate, NoteOut, parse_gpu_indices, serialize_gpu_indices


def future_time(*, days: int = 365) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


class NoteValidationTests(unittest.TestCase):
    def test_parse_gpu_indices_returns_sorted_unique_values(self) -> None:
        self.assertEqual(parse_gpu_indices('[3, 1, 3, 2]'), [1, 2, 3])

    def test_parse_gpu_indices_rejects_bool_negative_and_malformed_json(self) -> None:
        for raw in ('[true]', '[-1]', '{"gpu": 1}', 'not-json'):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_gpu_indices(raw)

    def test_serialize_gpu_indices_returns_canonical_json(self) -> None:
        self.assertEqual(serialize_gpu_indices([2, 0, 2]), '[0, 2]')

    def test_serialize_gpu_indices_rejects_bool_as_int(self) -> None:
        with self.assertRaises(ValueError):
            serialize_gpu_indices([True])

    def test_memo_rejects_non_empty_gpu_list(self) -> None:
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='memo',
                expires_at=future_time(),
                kind='memo',
                gpu_indices=[1],
            )

    def test_memo_defaults_remain_backward_compatible(self) -> None:
        note = NoteCreate(
            username='u',
            ssh_password='pw',
            content='memo',
            expires_at=future_time(),
        )
        self.assertEqual(note.kind, 'memo')
        self.assertEqual(note.gpu_indices, [])
        self.assertEqual(note.priority, 'normal')
        self.assertIsNone(note.display_name)

    def test_priority_accepts_supported_values(self) -> None:
        for priority in ('normal', 'high', 'urgent'):
            with self.subTest(priority=priority):
                note = NoteCreate(
                    username='u',
                    ssh_password='pw',
                    content='memo',
                    expires_at=future_time(),
                    priority=priority,
                )
                self.assertEqual(note.priority, priority)

    def test_priority_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='memo',
                expires_at=future_time(),
                priority='low',
            )

    def test_display_name_is_trimmed_and_blank_values_normalize_to_none(self) -> None:
        trimmed = NoteCreate(
            username='u',
            ssh_password='pw',
            content='memo',
            expires_at=future_time(),
            display_name='  Grace Hopper  ',
        )
        blank = NoteCreate(
            username='u',
            ssh_password='pw',
            content='memo',
            expires_at=future_time(),
            display_name='   ',
        )
        omitted = NoteCreate(
            username='u',
            ssh_password='pw',
            content='memo',
            expires_at=future_time(),
        )

        self.assertEqual(trimmed.display_name, 'Grace Hopper')
        self.assertIsNone(blank.display_name)
        self.assertIsNone(omitted.display_name)

    def test_display_name_rejects_values_longer_than_40_characters(self) -> None:
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='memo',
                expires_at=future_time(),
                display_name='x' * 41,
            )

    def test_hold_rejects_empty_gpu_list_and_non_integer_values(self) -> None:
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='hold',
                expires_at=future_time(),
                kind='hold',
                gpu_indices=[],
            )
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='hold',
                expires_at=future_time(),
                kind='hold',
                gpu_indices=[1, '2'],
            )
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='hold',
                expires_at=future_time(),
                kind='hold',
                gpu_indices=[True],
            )

    def test_hold_normalizes_gpu_indices(self) -> None:
        note = NoteCreate(
            username='u',
            ssh_password='pw',
            content='hold',
            expires_at=future_time(),
            kind='hold',
            gpu_indices=[3, 1, 3],
        )
        self.assertEqual(note.gpu_indices, [1, 3])

    def test_note_out_accepts_canonical_db_payload(self) -> None:
        out = NoteOut(
            id=1,
            server_id=7,
            username='u',
            display_name='Display Name',
            content='hold',
            created_at='2026-07-18T00:00:00Z',
            expires_at=None,
            priority='high',
            kind='hold',
            gpu_indices=parse_gpu_indices('[2, 0, 2]'),
        )
        self.assertEqual(out.priority, 'high')
        self.assertEqual(out.display_name, 'Display Name')
        self.assertEqual(out.gpu_indices, [0, 2])
        self.assertEqual(serialize_gpu_indices(out.gpu_indices), '[0, 2]')

    def test_note_out_normalizes_missing_priority_and_blank_display_name(self) -> None:
        out = NoteOut(
            id=1,
            server_id=7,
            username='u',
            display_name='   ',
            content='memo',
            created_at='2026-07-18T00:00:00Z',
            expires_at=None,
            priority=None,
            kind='memo',
            gpu_indices=[],
        )
        self.assertEqual(out.priority, 'normal')
        self.assertIsNone(out.display_name)

    def test_note_out_rejects_malformed_db_json(self) -> None:
        with self.assertRaises(ValidationError):
            NoteOut(
                id=1,
                server_id=7,
                username='u',
                display_name=None,
                content='hold',
                created_at='2026-07-18T00:00:00Z',
                expires_at=None,
                priority='urgent',
                kind='hold',
                gpu_indices='not-json',
            )


if __name__ == '__main__':
    unittest.main()
