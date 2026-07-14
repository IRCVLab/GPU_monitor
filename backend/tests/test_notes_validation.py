from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from backend.routers.notes import NoteCreate, NoteOut, parse_gpu_indices, serialize_gpu_indices


FUTURE = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


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
                expires_at=FUTURE,
                kind='memo',
                gpu_indices=[1],
            )

    def test_memo_defaults_remain_backward_compatible(self) -> None:
        note = NoteCreate(
            username='u',
            ssh_password='pw',
            content='memo',
            expires_at=FUTURE,
        )
        self.assertEqual(note.kind, 'memo')
        self.assertEqual(note.gpu_indices, [])

    def test_hold_rejects_empty_gpu_list_and_non_integer_values(self) -> None:
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='hold',
                expires_at=FUTURE,
                kind='hold',
                gpu_indices=[],
            )
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='hold',
                expires_at=FUTURE,
                kind='hold',
                gpu_indices=[1, '2'],
            )
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='hold',
                expires_at=FUTURE,
                kind='hold',
                gpu_indices=[True],
            )

    def test_hold_normalizes_gpu_indices(self) -> None:
        note = NoteCreate(
            username='u',
            ssh_password='pw',
            content='hold',
            expires_at=FUTURE,
            kind='hold',
            gpu_indices=[3, 1, 3],
        )
        self.assertEqual(note.gpu_indices, [1, 3])

    def test_note_out_accepts_canonical_db_payload(self) -> None:
        out = NoteOut(
            id=1,
            server_id=7,
            username='u',
            content='hold',
            created_at='2026-07-15T00:00:00Z',
            expires_at=None,
            kind='hold',
            gpu_indices=parse_gpu_indices('[2, 0, 2]'),
        )
        self.assertEqual(out.gpu_indices, [0, 2])
        self.assertEqual(serialize_gpu_indices(out.gpu_indices), '[0, 2]')

    def test_note_out_rejects_malformed_db_json(self) -> None:
        with self.assertRaises(ValidationError):
            NoteOut(
                id=1,
                server_id=7,
                username='u',
                content='hold',
                created_at='2026-07-15T00:00:00Z',
                expires_at=None,
                kind='hold',
                gpu_indices='not-json',
            )
