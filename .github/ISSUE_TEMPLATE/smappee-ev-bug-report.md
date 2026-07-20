---
name: Smappee EV Bug Report
about: Report a technical issue with the Smappee EV Home Assistant integration.
title: "[BUG]"
labels: bug
assignees: myny-git

---

## 📢 Before You Begin

❗ **Issues are *not* for general questions or how-tos.**  
👉 Please use the [Discussions section](https://github.com/myny-git/smappee_ev/discussions) to ask questions or share ideas.

## ⚠️ Check for the Latest Version in HACS

HACS does not always notify you of updates automatically. Please:

1. Go to `HACS` > Integrations in Home Assistant  
2. Search for `smappee`  
3. Click the 3-dot menu next to the integration  
4. Choose `Update Information`  
5. If an update is available, install it **before reporting this issue**

## ✅ Pre-Check Checklist

Please confirm **all** of the following before submitting:

- [ ] I confirm that no other conflicting Smappee EV integrations are installed or configured (including deactivated ones — the official Smappee integration does not count).
- [ ] My Home Assistant version is up to date.
- [ ] I am using the **latest version** of this integration (verified via HACS).
- [ ] I have reviewed [open and closed issues](https://github.com/myny-git/smappee_ev/issues?q=is%3Aissue) to avoid duplicates.
- [ ] I have enabled and prepared DEBUG log output (for bug reports).
- [ ] I have attached Smappee EV diagnostics below, or explained why diagnostics are unavailable.
- [ ] This is a bug report — not a general usage question.

## 📋 What are the steps to reproduce this issue?

1. …
2. …
3. …

## ❗ What happens?

...

## ✅ What were you expecting to happen?

...

## 🩺 Smappee EV diagnostics

Please attach diagnostics for the affected Smappee EV config entry whenever possible:

1. Go to `Settings` > `Devices & services`.
2. Select the Smappee EV integration.
3. Open the three-dot menu for the affected config entry and select `Download diagnostics`.
4. Open the downloaded JSON file in a text editor and verify that sensitive values are redacted.
5. Drag and drop the diagnostics JSON file below.

_Diagnostics provide the runtime topology, connector mapping and MQTT routing information needed to investigate most issues. Reports without diagnostics may take longer to diagnose._

<!-- Drag and drop the diagnostics JSON file here. -->

## 🪵 Any logs, error output, etc?

_Please paste any relevant **DEBUG log output** below:_

## 📎 Any other comments (or screenshots)?

...
