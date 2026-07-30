# -*- coding: utf-8 -*-
"""引擎自适应健康退避(2026-07-30,死源治理二期)。

起因:机房 IP 风控/CAPTCHA 触发后,SearXNG suspend 3600s + 网关每次照样六连打,
瞬时抖动被固化成持续"团灭"。本模块在网关侧加熔断层:

- searx_client.search 对账出的 engines_failed 逐引擎 record_failure();
  连续 ENGINE_FAIL_THRESHOLD 次失败 → degraded,进入指数退避
  (ENGINE_BACKOFF_BASE_S 起步翻倍,ENGINE_BACKOFF_MAX_S 封顶)
- 退避期内:默认集/场景集调用**剔除**该引擎(不再硬撞风控养 suspend);
  用户 engines= 显式点名**不剔除**(用户意图优先,失败如实入账)
- 退避到期:下一次调用放行一次探测;成功 record_success() 归零恢复,
  失败退避升级一档(翻倍)
- engines_no_results 不算失败(健康但零匹配/静默歧义,见 searx_client.reconcile)

账目:filter_engines() 返回 degraded 清单 [{engine, reason, retry_after_s}],
search/research 响应 meta.engines_degraded 如实携带(底线②,绝不静默剔除)。

进程内存态(与 fetch LRU 缓存同级,不持久化;重启清零——与 SearXNG suspend
同为内存语义,一致)。线程安全(asyncio 单事件循环 + ThreadPoolExecutor 混用)。
"""
import threading
import time

from api import config

_lock = threading.Lock()
# engine -> {"fails": 连续失败计数, "until": 退避到期 monotonic 秒(None=健康),
#            "level": 退避档位(决定时长), "reason": 最近失败原因}
_state: dict[str, dict] = {}


def _backoff_s(level: int) -> float:
    return min(config.ENGINE_BACKOFF_BASE_S * (2 ** level),
               config.ENGINE_BACKOFF_MAX_S)


def record_failure(engine: str, reason: str) -> None:
    """一次确定失败(unresponsive_engines 对账)入账;达阈值进入/升级退避。"""
    now = time.monotonic()
    with _lock:
        st = _state.setdefault(engine, {"fails": 0, "until": None, "level": -1,
                                        "reason": ""})
        st["fails"] += 1
        st["reason"] = reason[:200]
        if st["until"] is not None and now < st["until"]:
            return  # 退避期内的失败不改档(探测失败走 filter 侧升级)
        if st["fails"] >= config.ENGINE_FAIL_THRESHOLD:
            st["level"] += 1
            st["until"] = now + _backoff_s(st["level"])


def record_success(engine: str) -> None:
    """一次产出入账:归零恢复(含探测成功)。"""
    with _lock:
        st = _state.get(engine)
        if st:
            st.update({"fails": 0, "until": None, "level": -1, "reason": ""})


def note_probe_failure(engine: str) -> None:
    """探测失败(退避到期放行后再次失败):退避升级一档,重新计时。"""
    now = time.monotonic()
    with _lock:
        st = _state.setdefault(engine, {"fails": 0, "until": None, "level": -1,
                                        "reason": ""})
        st["level"] += 1
        st["fails"] = config.ENGINE_FAIL_THRESHOLD  # 保持熔断态
        st["until"] = now + _backoff_s(st["level"])


def filter_engines(engines: list[str]) -> tuple[list[str], list[dict]]:
    """默认集/场景集调用前的熔断过滤。返回 (放行列表, degraded 账目)。

    退避到期(until <= now)的引擎**放行**(探测),但账目标 probing=True——
    它仍处熔断态,成功才由 record_success 摘帽;调用方见其失败应 note_probe_failure。
    """
    now = time.monotonic()
    allowed: list[str] = []
    degraded: list[dict] = []
    with _lock:
        for e in engines:
            st = _state.get(e)
            if st and st["until"] is not None:
                if now < st["until"]:
                    degraded.append({
                        "engine": e,
                        "reason": st["reason"] or "consecutive failures",
                        "retry_after_s": int(st["until"] - now),
                        "probing": False,
                    })
                    continue
                degraded.append({  # 到期探测:放行但如实标注
                    "engine": e,
                    "reason": st["reason"] or "consecutive failures",
                    "retry_after_s": 0,
                    "probing": True,
                })
            allowed.append(e)
    return allowed, degraded


def reset() -> None:
    """测试用:清空全部状态。"""
    with _lock:
        _state.clear()
