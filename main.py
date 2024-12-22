from typing import List, Optional
from uuid import uuid4

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
)
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Column, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from mcs.main import MedicalCoderSwarm

# Initialize FastAPI app
app = FastAPI(
    title="Medical Coder Swarm API",
    description="Production-grade API for managing and running Medical Coder Swarms with complete tracking capabilities.",
    version="1.0.0",
)

# Database setup
DATABASE_URL = "sqlite:///./medical_coder.db"
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine
)
Base = declarative_base()


class RunRecord(Base):
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String, unique=True, index=True, nullable=False)
    patient_id = Column(String, index=True, nullable=False)
    output = Column(JSON, nullable=False)


Base.metadata.create_all(bind=engine)


# Dependency for database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Models
class PatientCase(BaseModel):
    patient_id: str = Field(
        ..., description="Unique identifier for the patient."
    )
    patient_documentation: str = Field(
        ..., description="Detailed patient documentation."
    )
    max_loops: int = Field(
        1, description="Maximum number of loops the swarm should run."
    )


class RunResponse(BaseModel):
    run_id: str = Field(
        ..., description="Unique identifier for the run."
    )
    output: dict = Field(
        ..., description="Output generated by the swarm."
    )


class RunQuery(BaseModel):
    run_id: Optional[str] = Field(
        None, description="Run ID to fetch a specific run."
    )
    patient_id: Optional[str] = Field(
        None,
        description="Patient ID to fetch runs associated with a specific patient.",
    )


@app.post(
    "/run/",
    response_model=RunResponse,
    summary="Run a single medical coding task",
)
def run_task(
    patient_case: PatientCase, db: Session = Depends(get_db)
):
    """
    Runs a single medical coding task for the given patient case.
    """
    logger.info("Starting a new medical coding task.")
    run_id = str(uuid4())

    try:
        swarm = MedicalCoderSwarm(
            patient_id=patient_case.patient_id,
            max_loops=patient_case.max_loops,
            patient_documentation=patient_case.patient_documentation,
            output_folder_path="reports",
        )

        # Run the swarm
        swarm.run(task=patient_case.patient_documentation)
        output = swarm.to_dict()

        # Save run details
        run_record = RunRecord(
            run_id=run_id,
            patient_id=patient_case.patient_id,
            output=output,
        )
        db.add(run_record)
        db.commit()
        db.refresh(run_record)

        logger.info(
            f"Task completed successfully with run_id={run_id}."
        )
        return RunResponse(run_id=run_id, output=output)

    except Exception as e:
        logger.error(f"Error occurred while running the task: {e}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while processing the task.",
        )


@app.post(
    "/run/batch/",
    response_model=List[RunResponse],
    summary="Run batched medical coding tasks",
)
def run_batch(
    patient_cases: List[PatientCase],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Runs multiple medical coding tasks in batch mode.
    """
    logger.info("Starting a batch of medical coding tasks.")
    responses = []

    def process_batch(patient_case):
        try:
            run_id = str(uuid4())
            swarm = MedicalCoderSwarm(
                patient_id=patient_case.patient_id,
                max_loops=patient_case.max_loops,
                patient_documentation=patient_case.patient_documentation,
                output_folder_path="reports",
            )

            # Run the swarm
            swarm.run(task=patient_case.patient_documentation)
            output = swarm.to_dict()

            # Save run details
            run_record = RunRecord(
                run_id=run_id,
                patient_id=patient_case.patient_id,
                output=output,
            )
            db.add(run_record)
            db.commit()
            db.refresh(run_record)

            responses.append(
                RunResponse(run_id=run_id, output=output)
            )
            logger.info(
                f"Task completed successfully for patient_id={patient_case.patient_id} with run_id={run_id}."
            )

        except Exception as e:
            logger.error(
                f"Error occurred while processing batch task for patient_id={patient_case.patient_id}: {e}"
            )

    for case in patient_cases:
        background_tasks.add_task(process_batch, case)

    return responses


@app.get("/history/", summary="Query patient case and run history")
def query_history(run_query: RunQuery, db: Session = Depends(get_db)):
    """
    Query the history of patient cases and run outputs based on run ID or patient ID.
    """
    logger.info("Querying patient case and run history.")
    if run_query.run_id:
        run_record = (
            db.query(RunRecord)
            .filter(RunRecord.run_id == run_query.run_id)
            .first()
        )
        if run_record:
            logger.info(f"Run found for run_id={run_query.run_id}.")
            return {
                "run_id": run_record.run_id,
                "patient_id": run_record.patient_id,
                "output": run_record.output,
            }
        else:
            logger.warning(f"Run ID {run_query.run_id} not found.")
            raise HTTPException(
                status_code=404, detail="Run ID not found."
            )

    if run_query.patient_id:
        results = (
            db.query(RunRecord)
            .filter(RunRecord.patient_id == run_query.patient_id)
            .all()
        )
        if results:
            logger.info(
                f"Runs found for patient_id={run_query.patient_id}."
            )
            return [
                {
                    "run_id": record.run_id,
                    "patient_id": record.patient_id,
                    "output": record.output,
                }
                for record in results
            ]
        else:
            logger.warning(
                f"No runs found for patient_id={run_query.patient_id}."
            )
            raise HTTPException(
                status_code=404,
                detail="No runs found for the given patient ID.",
            )

    logger.error("No query parameters provided for history lookup.")
    raise HTTPException(
        status_code=400,
        detail="At least one query parameter (run_id or patient_id) must be provided.",
    )


@app.get("/runs/", summary="List all runs")
def list_all_runs(db: Session = Depends(get_db)):
    """
    List all medical coding runs with their details.
    """
    logger.info("Listing all runs.")
    runs = db.query(RunRecord).all()
    return [
        {
            "run_id": record.run_id,
            "patient_id": record.patient_id,
            "output": record.output,
        }
        for record in runs
    ]


if __name__ == "__main__":
    import uvicorn

    logger.add(
        "medical_coder_api.log",
        rotation="1 MB",
        retention="7 days",
        level="INFO",
    )
    logger.info("Starting Medical Coder Swarm API.")
    uvicorn.run(app, host="0.0.0.0", port=8000)