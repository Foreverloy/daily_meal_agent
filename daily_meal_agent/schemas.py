from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr

Unit = Literal["g", "kg", "ml", "L"]
Meal = Literal["breakfast", "lunch", "dinner"]


class ModelSettings(BaseModel):
    api_key: SecretStr
    base_url: str
    model: str
    use_responses_api: bool


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    model_settings: ModelSettings


class ToolResult(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: Any


class ChatResponse(BaseModel):
    reply: str
    tool_results: list[ToolResult]


class Nutrition(BaseModel):
    energy_kcal: float
    protein_g: float | None = None
    carbohydrate_g: float | None = None
    fat_g: float | None = None
    sodium_mg: float | None = None
    calcium_mg: float | None = None
    vitamin_c_mg: float | None = None
    vitamin_d_ug: float | None = None


class FoodInput(BaseModel):
    name: str
    basis_unit: Literal["g", "ml"]
    nutrition_per_100: Nutrition


class FoodPatch(BaseModel):
    name: str | None = None
    # PATCH 只发送用户提供的指标，null 表示将该项改为未知。
    nutrition_per_100: dict[str, float | None] | None = None
    favorite: bool | None = None


class Ingredient(BaseModel):
    food_id: int
    quantity: float
    unit: Unit


class RecipeInput(BaseModel):
    name: str
    ingredients: list[Ingredient]


class GoalInput(BaseModel):
    energy_kcal: float
    protein_percent: float
    carbohydrate_percent: float
    fat_percent: float


class EntryPatch(BaseModel):
    date: str | None = None
    meal_type: Meal | None = None
    item_type: Literal["food", "recipe"] | None = None
    food_id: int | None = None
    recipe_id: int | None = None
    quantity: float | None = None
    unit: Unit | None = None
