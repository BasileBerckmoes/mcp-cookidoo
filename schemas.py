"""
Recipe Schemas

Pydantic models for custom recipe data validation.
"""

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TTSAction(BaseModel):
    """Standard cooking / mixing action — a play button on the TM7.

    Language-independent: the LLM supplies raw numbers (time in seconds, speed
    label, optional temperature/direction) and the server forwards them into
    the Cookidoo annotation. Step text can be any language.
    """

    kind: Literal["tts"] = "tts"
    time: int = Field(..., description="Duration in seconds", ge=1, le=32400)
    speed: str = Field(..., description="Speed setting label, e.g. '5' or '0.5'")
    temperature: Optional[int] = Field(
        default=None,
        description="Temperature in °C (discrete device-supported values only); omit for speed-only actions",
    )
    direction: Optional[Literal["reverse"]] = Field(
        default=None,
        description="'reverse' for Linkslauf; omit for the default clockwise direction",
    )


class SteamingAction(BaseModel):
    """Varoma steaming action. Accessory is always 'Varoma'."""

    kind: Literal["steaming"] = "steaming"
    time: int = Field(..., description="Duration in seconds", ge=1, le=32400)
    speed: str = Field(..., description="Speed setting label, e.g. '2'")


class BrowningAction(BaseModel):
    """Browning / searing action (Modus Anbraten).

    Temperature must be one of the five device-supported values (140, 145, 150,
    155, 160 °C); duration is capped at 30 min.
    """

    kind: Literal["browning"] = "browning"
    time: int = Field(..., description="Duration in seconds (max 1800)", ge=1, le=1800)
    temperature: int = Field(..., description="Temperature in °C; one of {140, 145, 150, 155, 160}")
    power: Literal["Intense", "Gentle"] = Field(..., description="Burner power")

    @field_validator("temperature")
    @classmethod
    def _valid_browning_temp(cls, v: int) -> int:
        if v not in {140, 145, 150, 155, 160}:
            raise ValueError("browning temperature must be one of {140, 145, 150, 155, 160}")
        return v


RecipeAction = Annotated[
    Union[TTSAction, SteamingAction, BrowningAction],
    Field(discriminator="kind"),
]


class RecipeStep(BaseModel):
    """A single recipe step: prose text plus an optional structured action.

    When ``action`` is set, the server builds the TM7 annotation directly from
    that structured record — no text parsing, so the step text can be in any
    language. When ``action`` is ``None``, the German regex parser tries to
    derive an annotation from ``text`` (backward-compatible for German steps).
    """

    text: str = Field(..., description="Step text, in any language", min_length=1)
    action: Optional[RecipeAction] = Field(
        default=None,
        description="Optional structured cooking action; language-independent",
    )


class CustomRecipe(BaseModel):
    """Model for a custom recipe to be created on Cookidoo."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Chocolate Chip Cookies",
                "ingredients": [
                    "200g flour",
                    "100g butter",
                    "100g sugar",
                    "1 egg",
                    "100g chocolate chips",
                ],
                "steps": [
                    "Mix butter and sugar until creamy",
                    "Add egg and mix well",
                    "Add flour and chocolate chips",
                    {
                        "text": "Bake at 180°C for 12 minutes",
                        "action": {"kind": "tts", "time": 720, "speed": "0", "temperature": 180},
                    },
                ],
                "servings": 6,
                "prep_time": 15,
                "total_time": 30,
                "hints": ["Don't overmix the dough", "Cookies will firm up as they cool"],
                "tools": ["TM7", "TM6", "TM5"],
            }
        }
    )

    name: str = Field(..., description="Recipe name", min_length=1, max_length=200)
    ingredients: list[str] = Field(
        ..., description="List of ingredients with quantities", min_length=1
    )
    steps: list[RecipeStep] = Field(
        ...,
        description="Recipe steps. Each entry may be a plain string (parsed as German text for backward compatibility) or a RecipeStep with an optional structured action for language-independent TM7 annotations.",
        min_length=1,
    )
    servings: int = Field(default=4, description="Number of servings", ge=1, le=20)
    prep_time: int = Field(
        default=30, description="Preparation time in minutes", ge=1, le=1440
    )
    total_time: int = Field(
        default=60, description="Total cooking time in minutes", ge=1, le=1440
    )
    hints: Optional[list[str]] = Field(
        default=None, description="Optional cooking tips or hints"
    )
    tools: list[str] = Field(
        default_factory=lambda: ["TM7", "TM6", "TM5"],
        description="Compatible Thermomix devices (e.g. TM5, TM6, TM7). Defaults to all three.",
    )

    @field_validator("steps", mode="before")
    @classmethod
    def _normalize_steps(cls, v):
        if not isinstance(v, list):
            return v
        return [{"text": s} if isinstance(s, str) else s for s in v]
