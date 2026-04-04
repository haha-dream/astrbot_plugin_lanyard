import asyncio
import json
from typing import Any, Optional, cast

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import MessageChain, filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_plugin_lanyard",
    "haha-dream",
    "基于 Lanyard 把你的活动推送到群聊",
    "v1.0.6",
)
class LanyardActivityNotifier(Star):
    """Lanyard 活动推送插件

    通过 HTTP 定时拉取 Lanyard API，监听 Discord 用户的活动状态变化，
    并将更新推送到配置的 QQ 群聊。
    """

    LANYARD_HTTP_URL_TEMPLATE = "https://api.lanyard.rest/v1/users/{user_id}"
    ActivityBrief = str | tuple[str, str]

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._pushed_activities: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._http_session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        """初始化插件，启动 HTTP 轮询"""
        user_id = str(self.config.get("user_id", "")).strip()
        if not user_id:
            logger.warning(
                "Lanyard 插件: 未配置 Discord 用户 ID，插件已禁用。请在配置中设置 user_id"
            )
            return

        logger.info("Lanyard 插件初始化中...")
        self._stop_event = asyncio.Event()
        self._http_session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._http_poll_loop())

    async def terminate(self):
        """终止插件，停止 HTTP 轮询"""
        logger.info("Lanyard 插件终止中...")
        self._stop_event.set()

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

        if self._http_session is not None:
            await self._http_session.close()
            self._http_session = None

    def _get_poll_interval(self) -> float:
        """获取轮询间隔（秒）"""
        value = self.config.get("poll_interval", 15)
        try:
            interval = float(value)
        except (TypeError, ValueError):
            interval = 15.0
        return max(5.0, interval)

    def _get_http_proxy(self) -> Optional[str]:
        """获取 HTTP 代理地址"""
        value = str(self.config.get("http_proxy", "")).strip()
        return value or None

    async def _http_poll_loop(self):
        """HTTP 主循环：定时拉取并处理数据"""
        user_id = str(self.config.get("user_id", "")).strip()
        if not user_id:
            logger.warning("Lanyard 插件: 未配置 Discord 用户 ID，无法启动 HTTP 轮询")
            return

        poll_interval = self._get_poll_interval()
        logger.info(f"Lanyard HTTP 轮询已启动，间隔 {poll_interval:g}s")

        while not self._stop_event.is_set():
            try:
                presence_data = await self._fetch_presence_data(user_id)
                if presence_data:
                    await self._check_and_push_update(presence_data)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"HTTP 轮询错误: {e}")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_interval)
            except TimeoutError:
                pass

    async def _fetch_presence_data(self, user_id: str) -> Optional[dict]:
        """通过 HTTP 获取用户 Presence 数据"""
        url = self.LANYARD_HTTP_URL_TEMPLATE.format(user_id=user_id)
        proxy = self._get_http_proxy()
        if self._http_session is None:
            self._http_session = aiohttp.ClientSession()

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with self._http_session.get(
                url, timeout=timeout, proxy=proxy
            ) as response:
                if response.status != 200:
                    logger.error(f"HTTP 拉取失败，状态码 {response.status}")
                    return None
                payload = await response.json(content_type=None)
        except aiohttp.ClientError as e:
            logger.error(f"HTTP 拉取失败，网络错误: {e}")
            return None
        except asyncio.TimeoutError:
            logger.error("HTTP 拉取失败，请求超时")
            return None
        except json.JSONDecodeError:
            logger.error("HTTP 拉取失败，响应不是合法 JSON")
            return None

        if not isinstance(payload, dict):
            logger.error("HTTP 拉取失败，响应结构异常")
            return None

        if payload.get("success") is False:
            logger.error("Lanyard API 返回失败状态")
            return None

        presence_data = payload.get("data")
        if not isinstance(presence_data, dict):
            logger.error("Lanyard API 返回数据缺失")
            return None

        return presence_data

    async def _check_and_push_update(self, presence_data: dict):
        """检查每个活动是否变化，仅推送新增或变更的活动"""
        username = self._get_username(presence_data)
        current_activities = self._collect_current_activities(presence_data)

        current_keys = {activity_key for activity_key, _, _ in current_activities}
        stale_keys = [
            activity_key
            for activity_key in self._pushed_activities
            if activity_key not in current_keys
        ]
        for activity_key in stale_keys:
            self._pushed_activities.pop(activity_key, None)

        pushed_count = 0
        for activity_key, fingerprint, activity_text in current_activities:
            if self._pushed_activities.get(activity_key) == fingerprint:
                continue
            pushed = await self._push_update(activity_text)
            if not pushed:
                continue
            self._pushed_activities[activity_key] = fingerprint
            pushed_count += 1

        if pushed_count:
            logger.info(f"{username} 有 {pushed_count} 个活动发生变化并已推送")

    def _collect_current_activities(
        self, presence_data: dict
    ) -> list[tuple[str, str, str]]:
        """收集当前活动的 key、指纹和待推送文本"""
        enable_activities = self._parse_enable_activities(
            self.config.get("enable_activities", [])
        )
        username = self._get_username(presence_data)
        activities = presence_data.get("activities", [])
        if not isinstance(activities, list):
            return []

        current_activities: list[tuple[str, str, str]] = []
        for activity in activities:
            if not isinstance(activity, dict):
                continue

            activity_type = activity.get("type", 6)
            if enable_activities and activity_type not in enable_activities:
                continue

            activity_brief = self._format_activity_brief(activity)
            if not activity_brief:
                continue

            activity_text = self._join_activities([activity_brief], username)
            activity_key = self._generate_activity_key(activity)
            fingerprint = self._generate_single_activity_fingerprint(activity_brief)
            current_activities.append((activity_key, fingerprint, activity_text))

        return current_activities

    def _generate_activity_key(self, activity: dict) -> str:
        """生成活动 key，用于标识同一条活动会话"""
        activity_type = str(activity.get("type", 6))
        activity_id = str(activity.get("id", "")).strip()
        app_id = str(activity.get("application_id", "")).strip()
        name = str(activity.get("name", "")).strip()
        created_at = str(activity.get("created_at", "")).strip()

        key_parts = [
            activity_type,
            activity_id or "-",
            app_id or "-",
            name or "-",
            created_at or "-",
        ]
        return "|".join(key_parts)

    def _generate_single_activity_fingerprint(
        self, activity_brief: ActivityBrief
    ) -> str:
        """生成单个活动指纹，用于检测该活动是否发生变化"""
        return json.dumps(activity_brief, sort_keys=True, ensure_ascii=False)

    async def _push_update(self, text: str) -> bool:
        """推送单条活动更新到 QQ 群聊"""
        qq_groups = self._parse_qq_groups(self.config.get("qq_groups", []))
        if not qq_groups:
            logger.warning("未配置 QQ 群号，跳过推送")
            return False

        if not text:
            return False

        text = "\u200b" + text + "\u200b"
        pushed = False

        for group_id in qq_groups:
            try:
                chain = MessageChain()
                chain.message(text)

                umo = await self._get_group_unified_msg_origin(group_id)
                if not umo:
                    logger.warning(
                        f"群 {group_id} 未缓存的 unified_msg_origin，跳过推送。请先在该群发送消息。"
                    )
                    continue

                context = cast(Any, self.context)
                await context.send_message(umo, chain)
                logger.info(f"已推送活动更新到群 {group_id}")
                pushed = True
            except Exception as e:
                logger.error(f"推送到群 {group_id} 失败: {e}")

        return pushed

    async def _get_group_unified_msg_origin(self, group_id: str) -> Optional[str]:
        """获取群的 unified_msg_origin，优先从缓存获取"""
        async with self._lock:
            group_origins = await self.get_kv_data("group_origins", {})
            if not isinstance(group_origins, dict):
                group_origins = {}

            origin = group_origins.get(group_id)
            return origin

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def _on_group_message(self, event: AstrMessageEvent):
        """监听群消息，自动缓存群的 unified_msg_origin"""
        if not hasattr(event, "get_group_id"):
            return

        group_id = event.get_group_id()
        if not group_id:
            return

        try:
            umo = getattr(event, "unified_msg_origin", None)
            if umo:
                async with self._lock:
                    group_origins = await self.get_kv_data("group_origins", {})
                    if not isinstance(group_origins, dict):
                        group_origins = {}
                    group_origins[str(group_id)] = umo
                    await self.put_kv_data("group_origins", group_origins)
                    logger.debug(f"已缓存群 {group_id} 的消息来源")
        except Exception as e:
            logger.debug(f"缓存群消息来源失败: {e}")

    def _parse_qq_groups(self, value: object) -> set[str]:
        """解析 QQ 群号列表配置"""
        if not isinstance(value, list):
            return set()
        return {str(x).strip() for x in value if str(x).strip()}

    def _parse_enable_activities(self, value: object) -> set[int]:
        """解析启用的活动类型配置"""
        if not isinstance(value, list):
            return set()
        result = set()
        for item in value:
            if isinstance(item, int):
                result.add(item)
                continue
            if isinstance(item, str):
                try:
                    result.add(int(item))
                except ValueError:
                    pass
        return result

    def _get_filter_config(self) -> dict:
        """获取过滤配置"""
        filter_config = self.config.get("filter_config", {})
        if not isinstance(filter_config, dict):
            filter_config = {}

        exclude_app_ids = filter_config.get("exclude_app_ids", [])
        if not isinstance(exclude_app_ids, list):
            exclude_app_ids = []

        exclude_fields = filter_config.get("exclude_fields", {})
        if not isinstance(exclude_fields, dict):
            exclude_fields = {}

        return {
            "exclude_app_ids": {
                str(x).strip() for x in exclude_app_ids if str(x).strip()
            },
            "exclude_fields": exclude_fields,
        }

    def _should_exclude_app(self, app_id: str) -> bool:
        """判断应用是否应该被排除"""
        filter_config = self._get_filter_config()
        return app_id in filter_config["exclude_app_ids"]

    def _should_include_field(
        self, activity_type: int, field_name: str, app_id: str
    ) -> bool:
        """判断字段是否应该被包含"""
        if self._should_exclude_app(app_id):
            return False

        filter_config = self._get_filter_config()
        excluded_fields = filter_config["exclude_fields"].get(field_name, [])

        if not isinstance(excluded_fields, list):
            excluded_fields = []

        return app_id not in {str(x).strip() for x in excluded_fields if str(x).strip()}

    def _format_presence(self, presence_data: dict) -> str:
        """格式化活动信息为可读的文本"""
        username = self._get_username(presence_data)
        discord_status = "offline"
        try:
            enable_activities = self._parse_enable_activities(
                self.config.get("enable_activities", [])
            )

            activities_info: list[LanyardActivityNotifier.ActivityBrief] = []

            activities = presence_data.get("activities", [])
            if isinstance(activities, list):
                for activity in activities:
                    if not isinstance(activity, dict):
                        continue

                    activity_type = activity.get("type", 6)

                    if enable_activities and activity_type not in enable_activities:
                        continue

                    activity_msg = self._format_activity_brief(activity)
                    if activity_msg:
                        activities_info.append(activity_msg)

            if activities_info:
                return self._join_activities(activities_info, username)

            discord_status = presence_data.get("discord_status", "offline")
            return f"{username} 的 Discord 状态: {discord_status}"

        except Exception as e:
            logger.error(f"格式化活动信息失败: {e}")
            return f"{username} 的 Discord 状态: {discord_status}"

    def _get_username(self, presence_data: dict) -> str:
        """获取用于展示的 Discord 用户名"""
        user = presence_data.get("discord_user", {})
        if not isinstance(user, dict):
            return "Unknown"

        display_name = str(user.get("display_name", "")).strip()
        if display_name:
            return display_name

        username = str(user.get("username", "")).strip()
        if username:
            return username

        return "Unknown"

    def _join_activities(self, activities: list[ActivityBrief], username: str) -> str:
        """合并多个活动信息"""
        if not activities:
            return ""

        lines = []

        for activity in activities:
            if isinstance(activity, tuple):
                modifier, verb_content = activity
                lines.append(f"{username} {modifier}{verb_content} 了")
            else:
                lines.append(f"{username} {activity} 了")

        return "\n".join(lines)

    def _format_activity_brief(self, activity: dict) -> ActivityBrief | None:
        """格式化单个活动（返回字符串或修饰词和动词+内容的元组）"""
        activity_name = "Unknown"
        try:
            activity_type = activity.get("type", 6)
            activity_name = activity.get("name", "Unknown")
            details = activity.get("details", "")
            state = activity.get("state", "")
            assets = activity.get("assets", {})
            app_id = activity.get("application_id", "")

            if app_id and self._should_exclude_app(app_id):
                return None

            if activity_type == 0:
                game_info_parts = [f"玩 {activity_name}"]

                if self._should_include_field(activity_type, "large_text", app_id):
                    if assets and assets.get("large_text"):
                        large_text = assets["large_text"].strip()
                        game_info_parts.append(large_text)

                if self._should_include_field(activity_type, "state", app_id):
                    if state:
                        state_text = state.strip()
                        game_info_parts.append(state_text)

                if self._should_include_field(activity_type, "details", app_id):
                    if details:
                        game_info_parts.append(details)

                game_info = " | ".join(game_info_parts)
                return ("开始", game_info)

            elif activity_type == 1:
                stream_info = f"直播 {activity_name}"
                if details and self._should_include_field(
                    activity_type, "details", app_id
                ):
                    stream_info += f" ({details})"
                return ("开始", stream_info)

            elif activity_type == 2:
                # Spotify
                if (
                    details
                    and state
                    and self._should_include_field(activity_type, "details", app_id)
                    and self._should_include_field(activity_type, "state", app_id)
                ):
                    return ("开始", f"听 {details} - {state}")
                elif details and self._should_include_field(
                    activity_type, "details", app_id
                ):
                    return ("开始", f"听 {details}")
                elif state and self._should_include_field(
                    activity_type, "state", app_id
                ):
                    return ("开始", f"听 {state}")
                return ("开始", f"听 {activity_name}")

            elif activity_type == 3:
                watching_info = f"看 {activity_name}"
                if details and self._should_include_field(
                    activity_type, "details", app_id
                ):
                    watching_info += f" ({details})"
                return ("开始", watching_info)

            elif activity_type == 4:
                if state and self._should_include_field(activity_type, "state", app_id):
                    return state
                return "自定义状态"

            elif activity_type == 5:
                competing_info = f"竞争 {activity_name}"
                if details and self._should_include_field(
                    activity_type, "details", app_id
                ):
                    competing_info += f" ({details})"
                return ("开始", competing_info)

            else:
                return ("开始", f"捣鼓 {activity_name}")

        except Exception as e:
            logger.error(f"格式化单个活动失败: {e}")
            return ("开始", f"捣鼓 {activity_name}")
