"""
Recipe Schemas

Pydantic models for custom recipe data validation.
"""

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Device-supported values, mirrored from cookidoo-api's ThermomixSpeed /
# ThermomixTemperature enums. The backend rejects anything else at upload time;
# validating here turns a late upload failure into an early, explicit error.
ALLOWED_SPEEDS: frozenset[str] = frozenset(
    {"soft"} | {f"{x / 2:g}" for x in range(1, 21)}  # soft, 0.5, 1, 1.5, ... 10
)
ALLOWED_TEMPERATURES: frozenset[int] = frozenset(
    {37, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 98, 100, 105, 110, 115, 120}
)

Speed = str
Temperature = Union[int, str]


def _check_speed(v: object) -> Optional[str]:
    if v is None:
        return v
    v = str(v).strip().lower()
    try:
        v = f"{float(v):g}"  # "4.0" / 4 -> "4", "0.50" -> "0.5"
    except ValueError:
        pass
    if v not in ALLOWED_SPEEDS:
        raise ValueError(
            "speed must be 'soft' or 0.5-10 in steps of 0.5 (e.g. '0.5', '4', '10')"
        )
    return v


def _check_temperature(v: Optional[Temperature]) -> Optional[Temperature]:
    if v is None:
        return v
    if isinstance(v, str):
        if v.strip().lower() == "varoma":
            return "varoma"
        if not v.strip().isdigit():
            raise ValueError("temperature must be an integer °C or 'varoma'")
        v = int(v.strip())
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError("temperature must be an integer °C or 'varoma'")
    if v not in ALLOWED_TEMPERATURES:
        raise ValueError(
            f"temperature must be 'varoma' or one of {sorted(ALLOWED_TEMPERATURES)} °C"
        )
    return v


class TTSAction(BaseModel):
    """Standard cooking / mixing action — a play button on the TM7.

    Language-independent: the LLM supplies raw numbers (time in seconds, speed
    label, optional temperature/direction) and the server forwards them into
    the Cookidoo annotation. Step text can be any language.
    """

    kind: Literal["tts"] = "tts"
    time: int = Field(..., description="Duration in seconds", ge=1, le=32400)
    speed: Speed = Field(..., description="'soft' or '0.5'-'10' in steps of 0.5")
    temperature: Optional[Temperature] = Field(
        default=None,
        description="°C (37, 40, 45 ... 120) or 'varoma'; omit for speed-only actions",
    )
    direction: Optional[Literal["reverse"]] = Field(
        default=None,
        description="'reverse' for Linkslauf; omit for the default clockwise direction",
    )

    @field_validator("speed", mode="before")
    @classmethod
    def _v_speed(cls, v):
        return _check_speed(v)

    @field_validator("temperature", mode="before")
    @classmethod
    def _v_temp(cls, v):
        return _check_temperature(v)


class SteamingAction(BaseModel):
    """Steaming mode (Varoma and/or simmering basket)."""

    kind: Literal["steaming"] = "steaming"
    time: int = Field(..., description="Duration in seconds", ge=1, le=32400)
    speed: Speed = Field(..., description="Speed label, e.g. '1' or '2'")
    accessory: Literal["Varoma", "SimmeringBasket", "VaromaAndSimmeringBasket"] = Field(
        default="Varoma",
        description="Which steaming accessory is in use",
    )
    direction: Optional[Literal["reverse"]] = Field(
        default=None,
        description="'reverse' for Linkslauf; omit for the default clockwise direction",
    )

    @field_validator("speed", mode="before")
    @classmethod
    def _v_speed(cls, v):
        return _check_speed(v)


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


class ModeAction(BaseModel):
    """Other TM7 modes: dough kneading, turbo, blend, warm up, rice cooker.

    EXPERIMENTAL: the field set mirrors cookidoo-api's ``CookidooModeAnnotation``;
    which fields each mode requires is not yet confirmed against the backend.
    Upload rolls back on rejection, so a wrong shape fails loudly.
    """

    kind: Literal["dough", "turbo", "blend", "warm_up", "rice_cooker"]
    time: Optional[int] = Field(default=None, description="Duration in seconds", ge=1, le=32400)
    speed: Optional[Speed] = Field(default=None, description="Speed label, if the mode takes one")
    temperature: Optional[Temperature] = Field(
        default=None, description="°C or 'varoma', if the mode takes one"
    )
    direction: Optional[Literal["reverse"]] = Field(
        default=None, description="'reverse' for Linkslauf, if the mode takes one"
    )

    @field_validator("speed", mode="before")
    @classmethod
    def _v_speed(cls, v):
        return _check_speed(v)

    @field_validator("temperature", mode="before")
    @classmethod
    def _v_temp(cls, v):
        return _check_temperature(v)


RecipeAction = Annotated[
    Union[TTSAction, SteamingAction, BrowningAction, ModeAction],
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
                        "text": "Cook 12 min at 100°C on speed 1",
                        "action": {"kind": "tts", "time": 720, "speed": "1", "temperature": 100},
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


# --- Surgical edits on an existing custom recipe (customize_custom_recipe) ---
#
# Step numbers are 1-based and always refer to the recipe *as read*, before
# any edit in the same call, so ops never shift each other's targets.


class ReplaceStepOp(BaseModel):
    op: Literal["replace_step"]
    step: int = Field(..., ge=1, description="1-based step number in the recipe as read")
    with_steps: list[RecipeStep] = Field(
        ..., alias="with", min_length=1, description="One or more steps that replace it"
    )
    model_config = ConfigDict(populate_by_name=True)


class InsertStepsOp(BaseModel):
    op: Literal["insert_steps"]
    after: int = Field(..., ge=0, description="Insert after this step number; 0 = at the start")
    steps: list[RecipeStep] = Field(..., min_length=1)


class DeleteStepsOp(BaseModel):
    op: Literal["delete_steps"]
    steps: list[int] = Field(..., min_length=1, description="1-based step numbers to delete")


class ReplaceIngredientOp(BaseModel):
    op: Literal["replace_ingredient"]
    old: str = Field(..., min_length=1, description="Exact current ingredient line")
    new: str = Field(..., min_length=1)


class AddIngredientOp(BaseModel):
    op: Literal["add_ingredient"]
    ingredient: str = Field(..., min_length=1)
    after: Optional[str] = Field(
        default=None, description="Exact ingredient line to insert after; omit to append"
    )


class RemoveIngredientOp(BaseModel):
    op: Literal["remove_ingredient"]
    ingredient: str = Field(..., min_length=1, description="Exact current ingredient line")


class SetHintsOp(BaseModel):
    op: Literal["set_hints"]
    hints: list[str] = Field(..., description="Replaces all hints; [] clears them")


class RenameOp(BaseModel):
    op: Literal["rename"]
    name: str = Field(..., min_length=1, max_length=200)


class SetMetaOp(BaseModel):
    """Servings and times. Changing servings does not rescale ingredients."""

    op: Literal["set_meta"]
    servings: Optional[int] = Field(default=None, ge=1, le=20)
    prep_time: Optional[int] = Field(default=None, ge=1, le=1440, description="Minutes")
    total_time: Optional[int] = Field(default=None, ge=1, le=1440, description="Minutes")


RecipeEdit = Annotated[
    Union[
        ReplaceStepOp,
        InsertStepsOp,
        DeleteStepsOp,
        ReplaceIngredientOp,
        AddIngredientOp,
        RemoveIngredientOp,
        SetHintsOp,
        RenameOp,
        SetMetaOp,
    ],
    Field(discriminator="op"),
]


class RecipeEdits(BaseModel):
    edits: list[RecipeEdit] = Field(..., min_length=1)
