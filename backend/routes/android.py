"""Android owner setup on the existing authenticated console boundary."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..hands import adb

router = APIRouter(prefix="/api/harness/android")


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serial: str = Field(min_length=1, max_length=200)
    driver_consent: bool
    allowed_apps: list[str] = Field(max_length=40)


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(max_length=100)


class Pair(Address):
    code: str = Field(pattern=r"^\d{6}$")


def service(request):
    if not request.app.state.harness:
        raise HTTPException(404, "The harness is disabled.")
    return request.app.state.harness.android


@router.get("")
async def status(request: Request):
    return service(request).status()


@router.get("/devices")
async def devices(request: Request):
    service(request)
    try:
        output = await adb.run(["devices", "-l"])
        found = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                found.append(
                    {
                        "serial": parts[0],
                        "state": parts[1],
                        "model": next((p[6:] for p in parts[2:] if p.startswith("model:")), "Android"),
                    }
                )
        return {"devices": found}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/download")
async def download(request: Request):
    import httpx

    service(request)
    try:
        return await adb.download_info()
    except (ValueError, httpx.HTTPError):
        raise HTTPException(
            503, "The official download could not be checked. Retry or use Google's SDK Platform Tools page."
        ) from None


@router.post("/settings")
async def settings(payload: Setup, request: Request):
    try:
        android = service(request)
        result = android.save(**payload.model_dump())
        await android.driver.close()
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/pair")
async def pair(payload: Pair, request: Request):
    service(request)
    try:
        return await adb.pair(payload.address, payload.code)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/connect")
async def connect(payload: Address, request: Request):
    service(request)
    try:
        return await adb.connect(payload.address)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/stop")
async def stop(request: Request):
    from ..live import takeover

    android = service(request)
    android.nodes.clear()
    result = takeover().take("android", android.config["serial"])
    await android.driver.close()
    return result
