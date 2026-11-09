// BehaviorNudgeHelper: the macOS-native half of behaviornudge's live nudge engine.
//
// Speaks newline-delimited JSON over stdin/stdout with the Python engine:
//   engine -> helper: {"cmd":"ping"} | {"cmd":"notify",...} | {"cmd":"withdraw","id":..} | {"cmd":"config",...}
//   helper -> engine: ready | pong | action | dismissed | front_app | calls | idle | error
//
// Call detection reads which processes are capturing microphone input via CoreAudio's
// per-process properties (macOS 14.2+). It never opens, records or stores audio.
// Technique credit: jhammant/maccap (MIT), Sources/CoreAudioUtil.swift + CallDetector.swift.

import AppKit
import CoreAudio
import Foundation
import UserNotifications

// MARK: - output

let outLock = NSLock()
func emit(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
          let s = String(data: data, encoding: .utf8) else { return }
    outLock.lock(); defer { outLock.unlock() }
    FileHandle.standardOutput.write((s + "\n").data(using: .utf8)!)
}

// MARK: - call detection (CoreAudio per-process input)

func audioProcessObjects() -> [AudioObjectID] {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr, size > 0 else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func uint32Prop(_ obj: AudioObjectID, _ selector: AudioObjectPropertySelector) -> UInt32 {
    var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var value: UInt32 = 0
    var size = UInt32(MemoryLayout<UInt32>.size)
    return AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, &value) == noErr ? value : 0
}

func bundleIDProp(_ obj: AudioObjectID) -> String? {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioProcessPropertyBundleID, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var cf: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, &cf) == noErr, let s = cf?.takeRetainedValue() else { return nil }
    return s as String
}

/// Bundle ids of allowlisted apps currently capturing microphone input.
func activeCallers(allowlist: [String]) -> [[String: Any]] {
    var seen = Set<String>()
    var out: [[String: Any]] = []
    for obj in audioProcessObjects() where uint32Prop(obj, kAudioProcessPropertyIsRunningInput) != 0 {
        let pid = Int32(bitPattern: uint32Prop(obj, kAudioProcessPropertyPID))
        let app = NSRunningApplication(processIdentifier: pid)
        let bid = bundleIDProp(obj) ?? app?.bundleIdentifier ?? ""
        let name = app?.localizedName ?? bid
        guard allowlist.contains(where: { !bid.isEmpty && bid.hasPrefix($0) }) else { continue }
        // report the parent app for helper processes (e.g. com.google.Chrome.helper -> com.google.Chrome)
        let match = allowlist.first(where: { bid.hasPrefix($0) }) ?? bid
        if seen.insert(match).inserted { out.append(["bundle_id": match, "name": name]) }
    }
    return out.sorted { ($0["bundle_id"] as! String) < ($1["bundle_id"] as! String) }
}

// MARK: - idle / lock

func idleSeconds() -> Double {
    CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: CGEventType(rawValue: ~0)!)
}

func screenLocked() -> Bool {
    guard let d = CGSessionCopyCurrentDictionary() as? [String: Any] else { return false }
    return (d["CGSSessionScreenIsLocked"] as? Bool) ?? false
}

// MARK: - notifications

let actionIDs: [(String, String)] = [("back_to_task", "Back to task"), ("too_soon", "Too soon"),
                                     ("legit", "Legit task"), ("snooze", "Snooze 30 min")]

func resolveSound(_ name: String?) -> UNNotificationSound? {
    guard let name, !name.isEmpty, name != "none" else { return nil }
    if name.contains(".") { return UNNotificationSound(named: UNNotificationSoundName(name)) }
    for dir in ["/System/Library/Sounds", NSHomeDirectory() + "/Library/Sounds"] {
        for ext in ["aiff", "caf", "wav"] where FileManager.default.fileExists(atPath: "\(dir)/\(name).\(ext)") {
            return UNNotificationSound(named: UNNotificationSoundName("\(name).\(ext)"))
        }
    }
    return UNNotificationSound(named: UNNotificationSoundName(name))
}

final class Helper: NSObject, UNUserNotificationCenterDelegate {
    var allowlist: [String] = []
    var lastCalls: [String] = ["<unset>"]
    let center = UNUserNotificationCenter.current()

    func start() {
        center.delegate = self
        let actions = actionIDs.map { UNNotificationAction(identifier: $0.0, title: $0.1, options: []) }
        let category = UNNotificationCategory(identifier: "nudge", actions: actions, intentIdentifiers: [],
                                              options: [.customDismissAction])
        center.setNotificationCategories([category])
        center.requestAuthorization(options: [.alert, .sound]) { granted, error in
            emit(["event": "ready", "authorized": granted, "error": error?.localizedDescription ?? NSNull()])
        }
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(appActivated(_:)),
                                                          name: NSWorkspace.didActivateApplicationNotification, object: nil)
        if let app = NSWorkspace.shared.frontmostApplication { emitFront(app) }
        Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in self?.checkCalls() }
        Timer.scheduledTimer(withTimeInterval: 10.0, repeats: true) { _ in
            emit(["event": "idle", "idle_s": (idleSeconds() * 10).rounded() / 10, "locked": screenLocked()])
        }
    }

    @objc func appActivated(_ note: Notification) {
        if let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication { emitFront(app) }
    }

    func emitFront(_ app: NSRunningApplication) {
        emit(["event": "front_app", "bundle_id": app.bundleIdentifier ?? "", "name": app.localizedName ?? ""])
    }

    func checkCalls() {
        guard !allowlist.isEmpty else { return }
        let active = activeCallers(allowlist: allowlist)
        let ids = active.map { $0["bundle_id"] as! String }
        if ids != lastCalls {
            lastCalls = ids
            emit(["event": "calls", "active": active])
        }
    }

    func handle(_ msg: [String: Any]) {
        switch msg["cmd"] as? String {
        case "ping":
            emit(["event": "pong"])
        case "diag":
            center.getNotificationSettings { st in
                self.center.getDeliveredNotifications { delivered in
                    func name(_ v: UNNotificationSetting) -> String {
                        switch v { case .enabled: return "enabled"; case .disabled: return "disabled"; case .notSupported: return "notSupported"; @unknown default: return "unknown" }
                    }
                    let style: String
                    switch st.alertStyle { case .none: style = "none"; case .banner: style = "banner"; case .alert: style = "alert"; @unknown default: style = "unknown" }
                    emit(["event": "diag", "authorization": st.authorizationStatus.rawValue, "alertStyle": style,
                          "alert": name(st.alertSetting), "sound": name(st.soundSetting),
                          "notificationCenter": name(st.notificationCenterSetting), "lockScreen": name(st.lockScreenSetting),
                          "timeSensitive": name(st.timeSensitiveSetting), "delivered": delivered.count])
                }
            }
        case "config":
            if let list = msg["call_allowlist"] as? [String] { allowlist = list; lastCalls = ["<unset>"]; checkCalls() }
        case "withdraw":
            if let id = msg["id"].map({ "\($0)" }) {
                center.removeDeliveredNotifications(withIdentifiers: [id])
                center.removePendingNotificationRequests(withIdentifiers: [id])
            }
        case "notify":
            let content = UNMutableNotificationContent()
            content.title = msg["title"] as? String ?? "behaviornudge"
            content.body = msg["body"] as? String ?? ""
            if let sub = msg["subtitle"] as? String { content.subtitle = sub }
            // "notice" = a plain heads-up from the engine (no nudge buttons, no feedback)
            content.categoryIdentifier = (msg["type"] as? String) == "notice" ? "" : "nudge"
            content.sound = resolveSound(msg["sound"] as? String)
            content.interruptionLevel = .active  // time-sensitive needs an entitlement this ad-hoc build lacks
            let id = msg["id"].map { "\($0)" } ?? UUID().uuidString
            content.userInfo = ["id": id]
            center.add(UNNotificationRequest(identifier: id, content: content, trigger: nil)) { error in
                if let error { emit(["event": "error", "detail": "notify \(id): \(error.localizedDescription)"]) }
            }
        default:
            emit(["event": "error", "detail": "unknown command \(msg["cmd"] ?? "nil")"])
        }
    }

    // Show banners even though this accessory app is technically "active".
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound, .list])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let id = response.notification.request.identifier
        switch response.actionIdentifier {
        case UNNotificationDismissActionIdentifier: emit(["event": "dismissed", "id": id])
        case UNNotificationDefaultActionIdentifier: emit(["event": "action", "id": id, "action": "opened"])
        default: emit(["event": "action", "id": id, "action": response.actionIdentifier])
        }
        completionHandler()
    }
}

// MARK: - main

setvbuf(stdout, nil, _IONBF, 0)
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let helper = Helper()
helper.start()

Thread.detachNewThread {
    while let line = readLine() {
        guard let data = line.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            emit(["event": "error", "detail": "bad json"]); continue
        }
        DispatchQueue.main.async { helper.handle(obj) }
    }
    // stdin closed: the engine is gone, so exit.
    DispatchQueue.main.async { exit(0) }
}

app.run()
