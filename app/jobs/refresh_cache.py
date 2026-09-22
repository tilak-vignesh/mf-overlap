from prefect import flow


@flow
async def refresh_stale_holdings() -> None:
    """Periodic Prefect flow: find holdings older than the staleness window and re-fetch them."""
    raise NotImplementedError
