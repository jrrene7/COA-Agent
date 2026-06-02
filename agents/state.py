from typing import TypedDict


class OutreachState(TypedDict):
    lead: dict           # {"company": str, "url": str}
    research_data: dict  # web intel + company profile
    marketing_data: dict # positioning + messaging angles
    sales_data: dict     # email draft + objection tips
    report_path: str     # absolute path of written report
