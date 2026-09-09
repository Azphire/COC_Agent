from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import select, update

from app.domain.character import Character, utc_now
from app.persistence.character_models import CharacterDraftRow, CharacterEventRow, CharacterRollRow
from app.persistence.database import Database

character_adapter = TypeAdapter(Character)


class VersionConflict(Exception):
    pass


class CharacterRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def list_all(self) -> list[Character]:
        async with self.database.sessions() as session:
            rows = (
                await session.scalars(
                    select(CharacterDraftRow).order_by(
                        CharacterDraftRow.updated_at.desc(), CharacterDraftRow.id
                    )
                )
            ).all()
            records = (
                await session.scalars(select(CharacterRollRow).order_by(CharacterRollRow.position))
            ).all()
            grouped: dict[str, list[dict]] = {}
            for record in records:
                grouped.setdefault(record.character_id, []).append(record.record)
            return [
                character_adapter.validate_python(
                    {**row.document, "roll_records": grouped.get(row.id, [])}
                )
                for row in rows
            ]

    async def get(self, character_id: UUID) -> Character | None:
        async with self.database.sessions() as session:
            row = await session.get(CharacterDraftRow, str(character_id))
            if row is None:
                return None
            records = await session.scalars(
                select(CharacterRollRow)
                .where(CharacterRollRow.character_id == str(character_id))
                .order_by(CharacterRollRow.position)
            )
            return character_adapter.validate_python(
                {**row.document, "roll_records": [record.record for record in records]}
            )

    async def save(
        self,
        character: Character,
        events: list[tuple[str, dict]],
        expected_version: int | None = None,
    ) -> None:
        data = dict(
            name=character.name,
            status=character.status,
            ruleset_id=character.ruleset_id,
            version=character.version,
            updated_at=character.updated_at,
            document=character.model_dump(mode="json", exclude={"roll_records"}),
        )
        async with self.database.sessions.begin() as session:
            if expected_version is None:
                session.add(CharacterDraftRow(id=str(character.id), **data))
                await session.flush()
                for position, record in enumerate(character.roll_records):
                    session.add(
                        CharacterRollRow(
                            id=str(record.id),
                            character_id=str(character.id),
                            position=position,
                            record=record.model_dump(mode="json"),
                        )
                    )
            else:
                result = await session.execute(
                    update(CharacterDraftRow)
                    .where(
                        CharacterDraftRow.id == str(character.id),
                        CharacterDraftRow.version == expected_version,
                    )
                    .values(**data)
                )
                if result.rowcount != 1:
                    raise VersionConflict
            for event_type, payload in events:
                session.add(
                    CharacterEventRow(
                        id=str(uuid4()),
                        character_id=str(character.id),
                        type=event_type,
                        occurred_at=utc_now(),
                        payload={"version": character.version, **payload},
                    )
                )
