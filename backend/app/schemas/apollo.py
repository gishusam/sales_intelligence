from pydantic import BaseModel, Field, model_validator


class ProspectSearchRequest(BaseModel):
    locations: list[str]
    business_types: list[str]
    employee_min: int
    employee_max: int
    decision_maker_titles: list[str] = Field(default_factory=list)
    decision_maker_seniorities: list[str] = Field(default_factory=list)
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)

    @model_validator(mode="after")
    def validate_employee_range(self):
        if self.employee_min > self.employee_max:
            raise ValueError(
                "employee_min cannot be greater than employee_max"
            )

        return self
