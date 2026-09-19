"""向页面注入音量控制通道。

网页游戏的声音来源不统一：
- HTML5 <audio>/<video>：可直接设 .volume/.muted
- Flash（Ruffle）：player.volume
- Unity WebGL：走 WebAudio，无通用音量接口

策略：宿主用 postMessage 统一下发 {type:'hgd-volume', value, muted}，
页面里的 index.html 分发给各自引擎；同时注入一段兜底脚本，
对未适配的音视频元素做动态补漏（游戏可能延迟创建 audio 元素）。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Slot
from PySide6.QtWebEngineWidgets import QWebEngineView


_INJECT_JS = r"""
(function () {
  if (window.__hgdVolumeReady) return;
  window.__hgdVolumeReady = true;
  window.__hgdVolume = 0.3;
  window.__hgdMuted = false;

  function apply(el) {
    if (!el) return;
    try {
      el.volume = window.__hgdMuted ? 0 : window.__hgdVolume;
      el.muted = window.__hgdMuted;
    } catch (e) {}
  }

  // 已存在的媒体元素
  function sweep() {
    try {
      var els = document.querySelectorAll('audio,video');
      for (var i = 0; i < els.length; i++) apply(els[i]);
    } catch (e) {}
  }

  // 游戏常在运行时才创建 audio/video：用 MutationObserver 持续补漏
  try {
    var mo = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var added = muts[i].addedNodes || [];
        for (var j = 0; j < added.length; j++) {
          var n = added[j];
          if (!n || n.nodeType !== 1) continue;
          if (n.tagName === 'AUDIO' || n.tagName === 'VIDEO') apply(n);
          if (n.querySelectorAll) {
            var sub = n.querySelectorAll('audio,video');
            for (var k = 0; k < sub.length; k++) apply(sub[k]);
          }
        }
      }
    });
    mo.observe(document.documentElement || document.body, {childList:true, subtree:true});
  } catch (e) {}

  window.addEventListener('message', function (e) {
    var d = (e && e.data) || {};
    if (d.type !== 'hgd-volume') return;
    window.__hgdVolume = (typeof d.value === 'number') ? (d.value / 100) : window.__hgdVolume;
    window.__hgdMuted = !!d.muted;
    sweep();
    // Unity/其它走 AudioContext 的游戏：尝试统一增益
    try {
      if (window.unityInstance && window.unityInstance.Module) {
        // Unity 未开放音量 API，这里无能为力，交给系统音量
      }
    } catch (err) {}
  });

  // 定时兜底扫描（有些引擎替换节点而不触发 MutationObserver 的语义变化）
  setInterval(sweep, 2000);
  sweep();
})();
"""


class VolumeBridge(QObject):
    """宿主 <-> 页面 的音量通道。"""

    def __init__(self, view: QWebEngineView):
        super().__init__()
        self.view = view
        self._value = 30
        self._muted = False

    def attach(self) -> None:
        """页面每次导航完成都要重新注入（SPA/iframe 会清空上下文）。"""
        try:
            self.view.loadFinished.connect(self._on_loaded)
        except Exception:
            pass

    @Slot(bool)
    def _on_loaded(self, ok: bool) -> None:
        if not ok:
            return
        self._inject()

    def _inject(self) -> None:
        try:
            self.view.page().runJavaScript(_INJECT_JS)
        except Exception:
            pass
        # 注入后立刻下发当前音量
        self.push()

    def set_volume(self, value: int, muted: bool = False) -> None:
        self._value = max(0, min(100, int(value)))
        self._muted = bool(muted)
        self.push()

    def push(self) -> None:
        js = (
            "window.postMessage({type:'hgd-volume', value:%d, muted:%s}, '*');"
            % (self._value, "true" if self._muted else "false")
        )
        try:
            self.view.page().runJavaScript(js)
        except Exception:
            pass
