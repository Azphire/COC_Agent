"""Optional authored card details. They never imply possession in a running module."""

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class DetailModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


BackgroundKey = Literal["appearance", "beliefs", "people", "places", "possessions", "traits"]
Text = Annotated[str, Field(max_length=2000)]


class Background(DetailModel):
    appearance: Text = ""
    beliefs: Text = ""
    people: Text = ""
    places: Text = ""
    possessions: Text = ""
    traits: Text = ""
    key_connection: BackgroundKey | None = None


class AssetDetail(DetailModel):
    description: str = Field(min_length=1, max_length=500)
    value: float = Field(ge=0, le=1e12)


class Finances(DetailModel):
    currency: str = "USD"
    level: str = ""
    cash: float = 0
    assets: float = 0
    spending: float = 0
    assets_lower_bound: bool = False


class EquipmentEntry(DetailModel):
    id: str = Field(default_factory=lambda: str(uuid4()), pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    catalog_id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    quantity: Annotated[StrictInt, Field(ge=1, le=100)] = 1
    notes: str = Field(default="", max_length=1000)
    # Per physical weapon, not a total shared by `quantity` copies.
    initial_ammo: Annotated[StrictInt, Field(ge=0, le=1000)] = 0
    initial_reserve: Annotated[StrictInt, Field(ge=0, le=1000)] = 0
