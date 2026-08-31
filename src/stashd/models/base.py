"""Declarative base and the column types shared by every table.

Identifiers and names are bounded strings; paths and free text are unbounded. Enums are
stored as their wire value in a TEXT column with a check constraint, which is cheaper to
evolve than a PostgreSQL enum type.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar

from sqlalchemy import BigInteger, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, mapped_column


def enum_column(enum: type[StrEnum], **kwargs: Any) -> Any:
    return mapped_column(
        Enum(
            enum,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=32,
            name=f"ck_{enum.__name__.lower()}",
            values_callable=lambda members: [member.value for member in members],
        ),
        **kwargs,
    )


Utc = Annotated[datetime, mapped_column(DateTime(timezone=True))]
Bytes = Annotated[int, mapped_column(BigInteger)]
Count = Annotated[int, mapped_column(Integer)]
Name = Annotated[str, mapped_column(String(255))]


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),
        int: BigInteger,
        str: Text,
    }
