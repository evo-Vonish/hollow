# -*- coding: utf-8 -*-
"""统一响应类。

给 JSON 响应的 Content-Type 显式带上 charset=utf-8。JSON 规范(RFC 8259)本就是
UTF-8、application/json 不需要 charset 参数,但 Windows PowerShell 5.1 的
Invoke-RestMethod 在响应不带 charset 时会用 Latin-1 解码 → 中文乱码。加上 charset
让这类非规范客户端也能正确解码(规范客户端忽略此参数,无副作用)。
"""
from fastapi.responses import JSONResponse


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"
