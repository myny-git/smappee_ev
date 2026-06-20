"""Shared loose payload aliases for Dashboard and MQTT JSON data."""

from __future__ import annotations

from typing import Any

type DashboardObject = dict[str, Any]
type DashboardObjectList = list[DashboardObject]
type HighLevelConfigMap = dict[int, DashboardObject]
type MqttPayload = dict[str, Any]
type RecentSession = DashboardObject
