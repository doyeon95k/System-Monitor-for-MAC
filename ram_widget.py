#!/usr/bin/env python3
"""macOS System Monitor Desktop Widget — Battery / CPU / RAM / GPU / Network / Disk / Process
   with tabbed UI, scrollable content, color-zoned graphs, and process kill"""

import psutil
import subprocess
import re
import os
import signal
import time
from collections import deque

import AppKit
import objc
from Foundation import NSTimer, NSObject

HISTORY         = 30
TICK            = 2.0
W               = 300
H               = 390
COLLAPSED_H     = 44
PAD             = 14
IW              = W - 2 * PAD
MAX_PROC_ROWS   = 30
PROC_ROW_H      = 28

THRESH_LO   = 60
THRESH_HI   = 85

GREEN  = (0.30, 0.69, 0.31)
YELLOW = (1.00, 0.80, 0.00)
RED    = (0.96, 0.26, 0.21)

PROC_COLORS = [
    (0.39, 0.71, 0.96),
    (0.96, 0.65, 0.14),
    (0.69, 0.40, 0.82),
    (0.94, 0.50, 0.50),
    (0.56, 0.82, 0.49),
]


# ── helpers ───────────────────────────────────────────────────

def _c(r, g, b, a=1.0):
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def level_rgb(pct):
    if pct < THRESH_LO:
        return GREEN
    if pct < THRESH_HI:
        return YELLOW
    return RED


def battery_rgb(pct):
    if pct > 40:
        return GREEN
    if pct > 15:
        return YELLOW
    return RED


def gpu_usage():
    try:
        out = subprocess.check_output(
            ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "AGXAccelerator"],
            text=True, timeout=2, stderr=subprocess.DEVNULL,
        )
        for pat in (
            r'"Device Utilization %"\s*=\s*(\d+)',
            r'"GPU Activity\(%\)"\s*=\s*(\d+)',
        ):
            m = re.search(pat, out)
            if m:
                return min(int(m.group(1)), 100)
    except Exception:
        pass
    return None


def get_disks():
    result = []
    for p in psutil.disk_partitions(all=False):
        mp = p.mountpoint
        if mp == "/" or mp.startswith("/Volumes/"):
            result.append(mp)
    return result[:6]


def scan_processes():
    """Single scan: returns (top5, proc_list) from one process_iter call."""
    merged = {}
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent']):
        try:
            cpu = p.info['cpu_percent'] or 0
            name = p.info['name']
            pid = p.info['pid']
            if name not in merged:
                merged[name] = {'cpu': 0, 'pids': []}
            merged[name]['cpu'] += cpu
            merged[name]['pids'].append(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    items = sorted(merged.items(), key=lambda x: x[1]['cpu'], reverse=True)
    top5 = [(n, d['cpu']) for n, d in items if d['cpu'] > 0][:5]
    proc_list = [(n, d['cpu'], d['pids']) for n, d in items
                 if d['cpu'] > 0.05][:MAX_PROC_ROWS]
    return top5, proc_list


def fmt_rate(bps):
    if bps >= 1048576:
        return f"{bps / 1048576:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def make_label(parent, x, y, w, h, text, font, color):
    lbl = AppKit.NSTextField.alloc().initWithFrame_(
        AppKit.NSMakeRect(x, y, w, h))
    lbl.setStringValue_(text)
    lbl.setBezeled_(False)
    lbl.setDrawsBackground_(False)
    lbl.setEditable_(False)
    lbl.setSelectable_(False)
    lbl.setFont_(font)
    lbl.setTextColor_(color)
    parent.addSubview_(lbl)
    return lbl


def make_bar(parent, y, view_list):
    bar_bg = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(PAD, y, IW, 12))
    bar_bg.setWantsLayer_(True)
    bar_bg.layer().setBackgroundColor_(_c(0.25, 0.25, 0.25).CGColor())
    bar_bg.layer().setCornerRadius_(3)
    parent.addSubview_(bar_bg)
    view_list.append(bar_bg)

    bar_fill = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, 0, 12))
    bar_fill.setWantsLayer_(True)
    bar_fill.layer().setCornerRadius_(3)
    bar_fill.layer().setBackgroundColor_(_c(*GREEN).CGColor())
    bar_bg.addSubview_(bar_fill)
    return bar_fill


# ── top-down layout helpers (FlippedView) ─────────────────────

def make_section(parent, y, text, font, color, view_list, last=False):
    lbl = make_label(parent, PAD, y, IW, 18, text, font, color)
    view_list.append(lbl)
    y += 22
    bar_fill = make_bar(parent, y, view_list)
    y += 16
    graph = GraphView.alloc().initWithFrame_(
        AppKit.NSMakeRect(PAD, y, IW, 50))
    parent.addSubview_(graph)
    view_list.append(graph)
    y += 50
    if not last:
        y += 10
    return y, lbl, bar_fill, graph


def make_net_section(parent, y, text, font, color, view_list, last=False):
    lbl = make_label(parent, PAD, y, IW, 18, text, font, color)
    view_list.append(lbl)
    y += 22
    graph = GraphView.alloc().initWithFrame_(
        AppKit.NSMakeRect(PAD, y, IW, 50))
    parent.addSubview_(graph)
    view_list.append(graph)
    y += 50
    if not last:
        y += 10
    return y, lbl, graph


def make_battery_section(parent, y, font, font_bold, font_small, color,
                         view_list):
    batt_lbl = make_label(parent, PAD, y, IW, 18, "Battery  --", font, color)
    view_list.append(batt_lbl)
    y += 22
    bar_fill = make_bar(parent, y, view_list)
    y += 20
    sub_lbl = make_label(parent, PAD, y, IW, 14, "Energy Impact",
                         font_small, _c(0.55, 0.55, 0.55))
    view_list.append(sub_lbl)
    y += 18
    chart_sz = 80
    chart = MultiDonutView.alloc().initWithFrame_(
        AppKit.NSMakeRect(PAD, y, chart_sz, chart_sz))
    parent.addSubview_(chart)
    view_list.append(chart)
    text_x = PAD + chart_sz + 10
    text_w = IW - chart_sz - 10
    legend_lbls = []
    for i in range(5):
        ly = y + 2 + i * 16
        ll = make_label(parent, text_x, ly, text_w, 14, "",
                        font_small, color)
        view_list.append(ll)
        legend_lbls.append(ll)
    y += chart_sz + 12
    return y, batt_lbl, bar_fill, chart, legend_lbls


def make_disk_entry(parent, y, font_bold, font_mono, color, view_list):
    chart_sz = 80
    text_x = PAD + chart_sz + 10
    text_w = IW - chart_sz - 10
    chart = DiskChartView.alloc().initWithFrame_(
        AppKit.NSMakeRect(PAD, y + 2, chart_sz, chart_sz))
    parent.addSubview_(chart)
    view_list.append(chart)
    name_lbl = make_label(parent, text_x, y + 6, text_w, 16, "",
                          font_bold, AppKit.NSColor.whiteColor())
    view_list.append(name_lbl)
    avail_lbl = make_label(parent, text_x, y + 26, text_w, 16, "",
                           font_mono, _c(0.55, 0.88, 0.55))
    view_list.append(avail_lbl)
    detail_lbl = make_label(parent, text_x, y + 44, text_w, 16, "",
                            font_mono, color)
    view_list.append(detail_lbl)
    pct_lbl = make_label(parent, text_x, y + 62, text_w, 16, "",
                         font_mono, color)
    view_list.append(pct_lbl)
    y += chart_sz + 12
    return y, chart, name_lbl, avail_lbl, detail_lbl, pct_lbl


def make_proc_row(parent, y, idx, delegate, font, font_small, view_list):
    """One process row: [name_lbl] [cpu_lbl] [Quit btn]."""
    btn_w = 40
    cpu_w = 55
    name_w = IW - cpu_w - btn_w - 8

    name_lbl = make_label(parent, PAD, y + 2, name_w, 18, "",
                          font, _c(0.85, 0.85, 0.85))
    name_lbl.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
    view_list.append(name_lbl)

    cpu_lbl = make_label(parent, PAD + name_w + 2, y + 2, cpu_w, 18, "",
                         font_small, _c(0.65, 0.65, 0.65))
    view_list.append(cpu_lbl)

    btn = AppKit.NSButton.alloc().initWithFrame_(
        AppKit.NSMakeRect(PAD + name_w + cpu_w + 6, y + 2, btn_w, 20))
    btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
    btn.setBordered_(False)
    btn.setWantsLayer_(True)
    btn.layer().setBackgroundColor_(_c(0.85, 0.25, 0.25).CGColor())
    btn.layer().setCornerRadius_(4)
    btn.setTitle_("Quit")
    btn.setFont_(AppKit.NSFont.boldSystemFontOfSize_(9))
    btn.setContentTintColor_(AppKit.NSColor.whiteColor())
    btn.setTag_(idx)
    btn.setTarget_(delegate)
    btn.setAction_(
        objc.selector(delegate.onKillProc_, signature=b"v@:@"))
    parent.addSubview_(btn)
    view_list.append(btn)

    # thin separator
    sep = AppKit.NSBox.alloc().initWithFrame_(
        AppKit.NSMakeRect(PAD, y + PROC_ROW_H - 1, IW, 1))
    sep.setBoxType_(AppKit.NSBoxSeparator)
    parent.addSubview_(sep)
    view_list.append(sep)

    return name_lbl, cpu_lbl, btn


def set_bar(bar_fill, pct, color_fn=None):
    if color_fn is None:
        color_fn = level_rgb
    r, g, b = color_fn(pct)
    bar_fill.setFrame_(AppKit.NSMakeRect(0, 0, IW * pct / 100, 12))
    bar_fill.layer().setBackgroundColor_(_c(r, g, b).CGColor())


def scroll_to_top(scroll):
    clip = scroll.contentView()
    clip.scrollToPoint_(AppKit.NSMakePoint(0, 0))
    scroll.reflectScrolledClipView_(clip)


# ── flipped view for scroll content ───────────────────────────

class FlippedView(AppKit.NSView):
    def isFlipped(self):
        return True


# ── multi-segment donut chart (battery energy impact) ─────────

class MultiDonutView(AppKit.NSView):

    def initWithFrame_(self, frame):
        self = objc.super(MultiDonutView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._segments = []
        self._center = ""
        self._sub = ""
        self.setWantsLayer_(True)
        return self

    @objc.python_method
    def set_data(self, segments, center_text, sub_text=""):
        self._segments = segments
        self._center = center_text
        self._sub = sub_text
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty):
        bnd = self.bounds()
        w, h = bnd.size.width, bnd.size.height
        cx, cy = w / 2, h / 2
        outer = min(cx, cy) - 2
        inner = outer * 0.6

        ring = AppKit.NSBezierPath.bezierPath()
        ring.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (cx, cy), outer, 0, 360, False)
        ring.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (cx, cy), inner, 360, 0, True)
        ring.closePath()
        _c(0.25, 0.25, 0.25).setFill()
        ring.fill()

        sa = 90
        for frac, (cr, cg, cb) in self._segments:
            if frac < 0.005:
                continue
            ea = sa - 360 * frac
            arc = AppKit.NSBezierPath.bezierPath()
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                (cx, cy), outer, sa, ea, True)
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                (cx, cy), inner, ea, sa, False)
            arc.closePath()
            _c(cr, cg, cb, 0.85).setFill()
            arc.fill()
            sa = ea

        main_str = AppKit.NSString.stringWithString_(self._center)
        main_attrs = {
            AppKit.NSFontAttributeName:
                AppKit.NSFont.boldSystemFontOfSize_(11),
            AppKit.NSForegroundColorAttributeName:
                AppKit.NSColor.whiteColor(),
        }
        msz = main_str.sizeWithAttributes_(main_attrs)
        my = cy + (2 if self._sub else -msz.height / 2)
        main_str.drawAtPoint_withAttributes_(
            AppKit.NSMakePoint(cx - msz.width / 2, my), main_attrs)

        if self._sub:
            sub_str = AppKit.NSString.stringWithString_(self._sub)
            sub_attrs = {
                AppKit.NSFontAttributeName:
                    AppKit.NSFont.systemFontOfSize_(8),
                AppKit.NSForegroundColorAttributeName: _c(0.6, 0.6, 0.6),
            }
            ssz = sub_str.sizeWithAttributes_(sub_attrs)
            sub_str.drawAtPoint_withAttributes_(
                AppKit.NSMakePoint(cx - ssz.width / 2, cy - 12), sub_attrs)


# ── donut chart for disk ──────────────────────────────────────

class DiskChartView(AppKit.NSView):

    def initWithFrame_(self, frame):
        self = objc.super(DiskChartView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._pct = 0
        self._avail = 0
        self.setWantsLayer_(True)
        return self

    @objc.python_method
    def set_data(self, pct, avail_gb):
        self._pct = pct
        self._avail = avail_gb
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty):
        bnd = self.bounds()
        w, h = bnd.size.width, bnd.size.height
        cx, cy = w / 2, h / 2
        outer = min(cx, cy) - 2
        inner = outer * 0.6

        ring = AppKit.NSBezierPath.bezierPath()
        ring.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (cx, cy), outer, 0, 360, False)
        ring.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (cx, cy), inner, 360, 0, True)
        ring.closePath()
        _c(0.25, 0.25, 0.25).setFill()
        ring.fill()

        if self._pct > 0.5:
            sa = 90
            ea = 90 - 360 * self._pct / 100
            arc = AppKit.NSBezierPath.bezierPath()
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                (cx, cy), outer, sa, ea, True)
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                (cx, cy), inner, ea, sa, False)
            arc.closePath()
            cr, cg, cb = level_rgb(self._pct)
            _c(cr, cg, cb, 0.85).setFill()
            arc.fill()

        gb_str = AppKit.NSString.stringWithString_(f"{self._avail:.0f}GB")
        gb_attrs = {
            AppKit.NSFontAttributeName:
                AppKit.NSFont.boldSystemFontOfSize_(12),
            AppKit.NSForegroundColorAttributeName:
                AppKit.NSColor.whiteColor(),
        }
        gb_sz = gb_str.sizeWithAttributes_(gb_attrs)
        gb_str.drawAtPoint_withAttributes_(
            AppKit.NSMakePoint(cx - gb_sz.width / 2, cy), gb_attrs)

        fr_str = AppKit.NSString.stringWithString_("free")
        fr_attrs = {
            AppKit.NSFontAttributeName:
                AppKit.NSFont.systemFontOfSize_(9),
            AppKit.NSForegroundColorAttributeName: _c(0.6, 0.6, 0.6),
        }
        fr_sz = fr_str.sizeWithAttributes_(fr_attrs)
        fr_str.drawAtPoint_withAttributes_(
            AppKit.NSMakePoint(cx - fr_sz.width / 2, cy - 14), fr_attrs)


# ── sparkline graph with color zones ──────────────────────────

class GraphView(AppKit.NSView):

    def initWithFrame_(self, frame):
        self = objc.super(GraphView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._vals = deque(maxlen=HISTORY)
        self._rgb = GREEN
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(4)
        self.layer().setMasksToBounds_(True)
        return self

    @objc.python_method
    def record(self, value, rgb):
        self._rgb = rgb
        self._vals.append(value)
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty):
        _c(0.13, 0.13, 0.13).setFill()
        AppKit.NSBezierPath.fillRect_(self.bounds())

        vals = list(self._vals)
        if len(vals) < 2:
            return

        bnd = self.bounds()
        bw, bh = bnd.size.width, bnd.size.height
        mg = 2
        gw, gh = bw - 2 * mg, bh - 2 * mg
        step = gw / (HISTORY - 1)
        sx = mg + (HISTORY - len(vals)) * step

        y_lo = mg + THRESH_LO / 100 * gh
        y_hi = mg + THRESH_HI / 100 * gh

        for ty, tc in ((y_lo, GREEN), (y_hi, RED)):
            _c(*tc, 0.35).setStroke()
            tl = AppKit.NSBezierPath.bezierPath()
            tl.moveToPoint_((mg, ty))
            tl.lineToPoint_((bw - mg, ty))
            tl.setLineWidth_(0.5)
            tl.stroke()

        fp = AppKit.NSBezierPath.bezierPath()
        fp.moveToPoint_((sx, mg))
        for i, v in enumerate(vals):
            fp.lineToPoint_((sx + i * step, mg + v / 100 * gh))
        fp.lineToPoint_((sx + (len(vals) - 1) * step, mg))
        fp.closePath()

        zones = [
            (0,    y_lo, GREEN),
            (y_lo, y_hi, YELLOW),
            (y_hi, bh,   RED),
        ]
        for y0, y1, (cr, cg, cb) in zones:
            AppKit.NSGraphicsContext.saveGraphicsState()
            AppKit.NSBezierPath.clipRect_(
                AppKit.NSMakeRect(0, y0, bw, y1 - y0))
            _c(cr, cg, cb, 0.25).setFill()
            fp.fill()
            AppKit.NSGraphicsContext.restoreGraphicsState()

        for i in range(1, len(vals)):
            x0 = sx + (i - 1) * step
            p0 = mg + vals[i - 1] / 100 * gh
            x1 = sx + i * step
            p1 = mg + vals[i] / 100 * gh
            seg = AppKit.NSBezierPath.bezierPath()
            seg.moveToPoint_((x0, p0))
            seg.lineToPoint_((x1, p1))
            seg.setLineWidth_(1.5)
            peak = max(vals[i - 1], vals[i])
            cr, cg, cb = level_rgb(peak)
            _c(cr, cg, cb).setStroke()
            seg.stroke()


# ── application delegate ──────────────────────────────────────

class AppDelegate(NSObject):

    def applicationDidFinishLaunching_(self, _note):
        psutil.cpu_percent(interval=None)
        for p in psutil.process_iter(['cpu_percent']):
            pass
        self._collapsed = False
        self._current_tab = 0
        self._prev_net = None
        self._prev_net_time = 0
        self._net_history = deque(maxlen=HISTORY)
        self._proc_data = []        # [(name, cpu, [pids]), ...]
        self._gpu_tick = 0
        self._gpu_cache = None
        build_ui(self)
        self._timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                TICK, self,
                objc.selector(self.onTick_, signature=b"v@:@"),
                None, True,
            )
        )
        self.onTick_(None)

    @objc.typedSelector(b"v@:@")
    def onTick_(self, _timer):
        # ── Battery ──────────────────────────────────────────
        batt = psutil.sensors_battery()
        if batt:
            pct = batt.percent
            plugged = batt.power_plugged
            secs = batt.secsleft
            if plugged:
                status = "Charging"
            elif secs and secs > 0:
                hrs = secs // 3600
                mins = (secs % 3600) // 60
                status = f"~{hrs}:{mins:02d} left"
            else:
                status = "On Battery"
            self._batt_lbl.setStringValue_(
                f"Battery  {pct:.0f}%  {status}")
            set_bar(self._batt_bar, pct, battery_rgb)
        else:
            self._batt_lbl.setStringValue_("Battery  N/A")

        top, procs = scan_processes()
        self._proc_data = procs
        total_cpu = sum(c for _, c in top)
        if total_cpu > 0:
            segs = []
            for i, (name, cpu) in enumerate(top):
                segs.append(
                    (cpu / total_cpu,
                     PROC_COLORS[i % len(PROC_COLORS)]))
                self._legend_lbls[i].setStringValue_(
                    f"  {name[:12]}  {cpu:.1f}%")
                self._legend_lbls[i].setTextColor_(
                    _c(*PROC_COLORS[i % len(PROC_COLORS)]))
            for i in range(len(top), 5):
                self._legend_lbls[i].setStringValue_("")
            self._batt_chart.set_data(
                segs, f"{total_cpu:.0f}%", "CPU")
        else:
            self._batt_chart.set_data([], "0%", "CPU")
            for ll in self._legend_lbls:
                ll.setStringValue_("")

        # ── CPU ──────────────────────────────────────────────
        cpu = psutil.cpu_percent(interval=None)
        self._cpu_lbl.setStringValue_(f"CPU   {cpu:5.1f} %")
        set_bar(self._cpu_bar, cpu)
        self._cpu_g.record(cpu, level_rgb(cpu))

        # ── RAM ──────────────────────────────────────────────
        mem = psutil.virtual_memory()
        used = mem.used / 1073741824
        total_ram = mem.total / 1073741824
        rpct = mem.percent
        self._ram_lbl.setStringValue_(
            f"RAM   {used:.1f}/{total_ram:.1f} GB ({rpct:.1f}%)")
        set_bar(self._ram_bar, rpct)
        self._ram_g.record(rpct, level_rgb(rpct))

        # ── GPU (poll every other tick to reduce subprocess overhead)
        self._gpu_tick += 1
        if self._gpu_tick % 2 == 0:
            self._gpu_cache = gpu_usage()
        gpct = self._gpu_cache
        if gpct is not None:
            self._gpu_lbl.setStringValue_(f"GPU   {gpct:5.1f} %")
            set_bar(self._gpu_bar, gpct)
            self._gpu_g.record(gpct, level_rgb(gpct))
        else:
            self._gpu_lbl.setStringValue_("GPU   N/A")

        # ── Network ──────────────────────────────────────────
        net = psutil.net_io_counters()
        now = time.time()
        if self._prev_net is not None:
            dt = now - self._prev_net_time
            if dt > 0:
                dl = (net.bytes_recv - self._prev_net[0]) / dt
                ul = (net.bytes_sent - self._prev_net[1]) / dt
            else:
                dl = ul = 0
        else:
            dl = ul = 0
        self._prev_net = (net.bytes_recv, net.bytes_sent)
        self._prev_net_time = now

        self._net_lbl.setStringValue_(
            f"NET  \u2193{fmt_rate(dl)}  \u2191{fmt_rate(ul)}")
        self._net_history.append(dl)
        net_max = max(
            (max(self._net_history) if self._net_history else 1024),
            1024)
        net_pct = min(dl / net_max * 100, 100)
        self._net_g.record(net_pct, GREEN)

        # ── Disk ─────────────────────────────────────────────
        for mp, chart, name_lbl, avail_lbl, detail_lbl, pct_lbl \
                in self._disk_entries:
            try:
                u = psutil.disk_usage(mp)
                used_gb  = u.used / 1073741824
                free_gb  = u.free / 1073741824
                total_gb = u.total / 1073741824
                dpct     = u.percent
                display  = mp if mp == "/" else mp.split("/")[-1]
                name_lbl.setStringValue_(display)
                avail_lbl.setStringValue_(f"Avail {free_gb:.1f} GB")
                detail_lbl.setStringValue_(
                    f"Used {used_gb:.1f} / {total_gb:.1f} GB")
                pct_lbl.setStringValue_(f"Usage {dpct:.1f} %")
                chart.set_data(dpct, free_gb)
            except Exception:
                detail_lbl.setStringValue_("N/A")

        # ── Process tab ──────────────────────────────────────
        # procs already set above from scan_processes()
        # update data only (text + color)
        for i in range(MAX_PROC_ROWS):
            name_lbl, cpu_lbl, btn = self._proc_slots[i]
            if i < len(procs):
                pname, pcpu, _pids = procs[i]
                name_lbl.setStringValue_(pname)
                cpu_lbl.setStringValue_(f"{pcpu:.1f}%")
                cpu_lbl.setTextColor_(_c(*level_rgb(pcpu)))
        # calc height
        visible = min(len(procs), MAX_PROC_ROWS)
        self._proc_h = 30 + visible * PROC_ROW_H + 8
        # only touch visibility when process tab is active
        if self._current_tab == 2 and not self._collapsed:
            for i in range(MAX_PROC_ROWS):
                name_lbl, cpu_lbl, btn = self._proc_slots[i]
                h = (i >= visible)
                name_lbl.setHidden_(h)
                cpu_lbl.setHidden_(h)
                btn.setHidden_(h)
                sep_idx = i * 4 + 3
                if sep_idx < len(self._proc_views):
                    self._proc_views[sep_idx].setHidden_(h)
            vis_h = self._scroll.frame().size.height
            self._doc.setFrameSize_(
                AppKit.NSMakeSize(W, max(self._proc_h, vis_h)))

    @objc.typedSelector(b"v@:@")
    def onKillProc_(self, sender):
        idx = sender.tag()
        if idx < len(self._proc_data):
            _name, _cpu, pids = self._proc_data[idx]
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    pass

    @objc.typedSelector(b"v@:@")
    def onTabChanged_(self, _sender):
        seg = self._tab_ctrl.selectedSegment()
        self._current_tab = seg
        all_groups = [
            (self._sys_views, self._sys_h),
            (self._disk_views, self._disk_h),
            (self._proc_views, self._proc_h),
        ]
        for i, (views, _) in enumerate(all_groups):
            hidden = (i != seg)
            for v in views:
                v.setHidden_(hidden)
        # for proc tab, re-hide empty rows
        if seg == 2:
            visible = min(len(self._proc_data), MAX_PROC_ROWS)
            for i in range(MAX_PROC_ROWS):
                name_lbl, cpu_lbl, btn = self._proc_slots[i]
                h = (i >= visible)
                name_lbl.setHidden_(h)
                cpu_lbl.setHidden_(h)
                btn.setHidden_(h)
                sep_idx = i * 4 + 3
                if sep_idx < len(self._proc_views):
                    self._proc_views[sep_idx].setHidden_(h)

        new_h = all_groups[seg][1]
        vis_h = self._scroll.frame().size.height
        self._doc.setFrameSize_(AppKit.NSMakeSize(W, max(new_h, vis_h)))
        scroll_to_top(self._scroll)

    @objc.typedSelector(b"v@:@")
    def onToggle_(self, _sender):
        self._collapsed = not self._collapsed
        all_body = (self._tab_views + self._sys_views
                    + self._disk_views + self._proc_views)
        if self._collapsed:
            for v in all_body:
                v.setHidden_(True)
        else:
            for v in self._tab_views:
                v.setHidden_(False)
            groups = [self._sys_views, self._disk_views, self._proc_views]
            for i, grp in enumerate(groups):
                hidden = (i != self._current_tab)
                for v in grp:
                    v.setHidden_(hidden)
            # re-apply empty row hiding for proc tab
            if self._current_tab == 2:
                visible = min(len(self._proc_data), MAX_PROC_ROWS)
                for i in range(MAX_PROC_ROWS):
                    name_lbl, cpu_lbl, btn = self._proc_slots[i]
                    h = (i >= visible)
                    name_lbl.setHidden_(h)
                    cpu_lbl.setHidden_(h)
                    btn.setHidden_(h)

        frame = self._win.frame()
        top = frame.origin.y + frame.size.height
        new_h = COLLAPSED_H if self._collapsed else H
        new_frame = AppKit.NSMakeRect(
            frame.origin.x, top - new_h, W, new_h)
        self._win.setFrame_display_animate_(new_frame, True, True)
        self._toggle_btn.setTitle_(
            "\u25b4" if self._collapsed else "\u25be")

    @objc.typedSelector(b"v@:@")
    def onMenuToggle_(self, _sender):
        if self._win.isVisible():
            self._win.orderOut_(None)
            self._menu_toggle.setTitle_("Show Widget")
        else:
            self._win.makeKeyAndOrderFront_(None)
            self._menu_toggle.setTitle_("Hide Widget")

    @objc.typedSelector(b"v@:@")
    def onQuit_(self, _sender):
        AppKit.NSApp.terminate_(None)


# ── build UI ──────────────────────────────────────────────────

def build_ui(delegate):
    scr  = AppKit.NSScreen.mainScreen().frame()
    rect = AppKit.NSMakeRect(scr.size.width - W - 30,
                              scr.size.height - H - 50, W, H)

    win = AppKit.NSWindow.alloc() \
        .initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered, False)
    win.setLevel_(AppKit.NSFloatingWindowLevel)
    win.setOpaque_(False)
    win.setBackgroundColor_(_c(0.17, 0.17, 0.17, 0.95))
    win.setMovableByWindowBackground_(True)
    win.setHasShadow_(True)

    try:
        dark = AppKit.NSAppearance.appearanceNamed_(
            "NSAppearanceNameDarkAqua")
        if dark:
            win.setAppearance_(dark)
    except Exception:
        pass

    cv = win.contentView()
    cv.setWantsLayer_(True)
    cv.layer().setCornerRadius_(12)
    cv.layer().setMasksToBounds_(True)
    delegate._win = win

    mono = (AppKit.NSFont.fontWithName_size_("Menlo", 11)
            or AppKit.NSFont.monospacedSystemFontOfSize_weight_(11, 0))
    mono_bold = AppKit.NSFont.boldSystemFontOfSize_(12)
    font_small = AppKit.NSFont.systemFontOfSize_(10)
    font_proc = AppKit.NSFont.systemFontOfSize_(11)
    gray = _c(0.80, 0.80, 0.80)

    y = H - PAD

    # ── title bar ─────────────────────────────────────────────
    y -= 20
    title_lbl = make_label(cv, PAD, y, IW - 70, 20, "System Monitor",
                           AppKit.NSFont.boldSystemFontOfSize_(14),
                           AppKit.NSColor.whiteColor())
    title_lbl.setAutoresizingMask_(AppKit.NSViewMinYMargin)

    toggle = AppKit.NSButton.alloc().initWithFrame_(
        AppKit.NSMakeRect(W - PAD - 54, y, 24, 24))
    toggle.setBezelStyle_(AppKit.NSBezelStyleCircular)
    toggle.setTitle_("\u25be")
    toggle.setTarget_(delegate)
    toggle.setAction_(
        objc.selector(delegate.onToggle_, signature=b"v@:@"))
    toggle.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    cv.addSubview_(toggle)
    delegate._toggle_btn = toggle

    close_btn = AppKit.NSButton.alloc().initWithFrame_(
        AppKit.NSMakeRect(W - PAD - 24, y, 24, 24))
    close_btn.setBezelStyle_(AppKit.NSBezelStyleCircular)
    close_btn.setTitle_("X")
    close_btn.setTarget_(delegate)
    close_btn.setAction_(
        objc.selector(delegate.onQuit_, signature=b"v@:@"))
    close_btn.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    cv.addSubview_(close_btn)

    # ── body ──────────────────────────────────────────────────
    delegate._tab_views = []

    y -= 8
    sep = AppKit.NSBox.alloc().initWithFrame_(
        AppKit.NSMakeRect(PAD, y, IW, 1))
    sep.setBoxType_(AppKit.NSBoxSeparator)
    cv.addSubview_(sep)
    delegate._tab_views.append(sep)
    y -= 6

    # 3 tabs: System / Disk / Process
    seg_w = (IW - 4) / 3
    tab_ctrl = AppKit.NSSegmentedControl.alloc().initWithFrame_(
        AppKit.NSMakeRect(PAD, y - 24, IW, 24))
    tab_ctrl.setSegmentCount_(3)
    tab_ctrl.setLabel_forSegment_("System", 0)
    tab_ctrl.setLabel_forSegment_("Disk", 1)
    tab_ctrl.setLabel_forSegment_("Process", 2)
    tab_ctrl.setWidth_forSegment_(seg_w, 0)
    tab_ctrl.setWidth_forSegment_(seg_w, 1)
    tab_ctrl.setWidth_forSegment_(seg_w, 2)
    tab_ctrl.setSelectedSegment_(0)
    tab_ctrl.setTarget_(delegate)
    tab_ctrl.setAction_(
        objc.selector(delegate.onTabChanged_, signature=b"v@:@"))
    cv.addSubview_(tab_ctrl)
    delegate._tab_ctrl = tab_ctrl
    delegate._tab_views.append(tab_ctrl)
    y -= 30

    # ── scroll view ───────────────────────────────────────────
    scroll_h = y
    scroll = AppKit.NSScrollView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, W, scroll_h))
    scroll.setHasVerticalScroller_(True)
    scroll.setHasHorizontalScroller_(False)
    scroll.setAutohidesScrollers_(True)
    scroll.setDrawsBackground_(False)
    scroll.setBorderType_(AppKit.NSNoBorder)
    scroll.setScrollerKnobStyle_(AppKit.NSScrollerKnobStyleLight)
    cv.addSubview_(scroll)
    delegate._tab_views.append(scroll)
    delegate._scroll = scroll

    doc = FlippedView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, W, scroll_h))
    scroll.setDocumentView_(doc)
    delegate._doc = doc

    # ── system tab ────────────────────────────────────────────
    delegate._sys_views = []
    sy = 6

    sy, delegate._batt_lbl, delegate._batt_bar, \
        delegate._batt_chart, delegate._legend_lbls = \
        make_battery_section(doc, sy, mono, mono_bold, font_small,
                             gray, delegate._sys_views)
    sy, delegate._cpu_lbl, delegate._cpu_bar, delegate._cpu_g = \
        make_section(doc, sy, "CPU  --", mono, gray,
                     delegate._sys_views)
    sy, delegate._ram_lbl, delegate._ram_bar, delegate._ram_g = \
        make_section(doc, sy, "RAM  --", mono, gray,
                     delegate._sys_views)
    sy, delegate._gpu_lbl, delegate._gpu_bar, delegate._gpu_g = \
        make_section(doc, sy, "GPU  --", mono, gray,
                     delegate._sys_views)
    sy, delegate._net_lbl, delegate._net_g = \
        make_net_section(doc, sy, "NET  --", mono, gray,
                         delegate._sys_views, last=True)

    delegate._sys_h = sy + 8

    # ── disk tab ──────────────────────────────────────────────
    delegate._disk_views = []
    delegate._disk_entries = []
    dy = 6
    disks = get_disks()
    for mountpoint in disks:
        dy, chart, name_lbl, avail_lbl, detail_lbl, pct_lbl = \
            make_disk_entry(doc, dy, mono_bold, mono, gray,
                            delegate._disk_views)
        delegate._disk_entries.append(
            (mountpoint, chart, name_lbl, avail_lbl,
             detail_lbl, pct_lbl))
    delegate._disk_h = dy + 8

    for v in delegate._disk_views:
        v.setHidden_(True)

    # ── process tab ───────────────────────────────────────────
    delegate._proc_views = []
    delegate._proc_slots = []       # [(name_lbl, cpu_lbl, btn), ...]
    py = 6
    # column header
    hdr_name = make_label(doc, PAD, py, IW - 100, 16, "Name",
                          AppKit.NSFont.boldSystemFontOfSize_(10),
                          _c(0.55, 0.55, 0.55))
    delegate._proc_views.append(hdr_name)
    hdr_cpu = make_label(doc, PAD + IW - 100, py, 50, 16, "CPU%",
                         AppKit.NSFont.boldSystemFontOfSize_(10),
                         _c(0.55, 0.55, 0.55))
    delegate._proc_views.append(hdr_cpu)
    py += 20

    hsep = AppKit.NSBox.alloc().initWithFrame_(
        AppKit.NSMakeRect(PAD, py, IW, 1))
    hsep.setBoxType_(AppKit.NSBoxSeparator)
    doc.addSubview_(hsep)
    delegate._proc_views.append(hsep)
    py += 4

    for i in range(MAX_PROC_ROWS):
        name_lbl, cpu_lbl, btn = make_proc_row(
            doc, py + i * PROC_ROW_H, i, delegate,
            font_proc, font_small, delegate._proc_views)
        delegate._proc_slots.append((name_lbl, cpu_lbl, btn))

    delegate._proc_h = py + MAX_PROC_ROWS * PROC_ROW_H + 8

    for v in delegate._proc_views:
        v.setHidden_(True)

    # set initial doc size for system tab
    doc.setFrameSize_(
        AppKit.NSMakeSize(W, max(delegate._sys_h, scroll_h)))

    # ── menu bar ──────────────────────────────────────────────
    status_bar = AppKit.NSStatusBar.systemStatusBar()
    status_item = status_bar.statusItemWithLength_(
        AppKit.NSVariableStatusItemLength)
    status_item.button().setTitle_("SysMon")
    status_item.button().setFont_(
        AppKit.NSFont.monospacedSystemFontOfSize_weight_(12, 0.2))

    menu = AppKit.NSMenu.alloc().init()

    toggle_item = AppKit.NSMenuItem.alloc() \
        .initWithTitle_action_keyEquivalent_(
            "Hide Widget",
            objc.selector(delegate.onMenuToggle_, signature=b"v@:@"),
            "")
    toggle_item.setTarget_(delegate)
    menu.addItem_(toggle_item)
    delegate._menu_toggle = toggle_item

    collapse_item = AppKit.NSMenuItem.alloc() \
        .initWithTitle_action_keyEquivalent_(
            "Collapse / Expand",
            objc.selector(delegate.onToggle_, signature=b"v@:@"), "")
    collapse_item.setTarget_(delegate)
    menu.addItem_(collapse_item)

    menu.addItem_(AppKit.NSMenuItem.separatorItem())

    quit_item = AppKit.NSMenuItem.alloc() \
        .initWithTitle_action_keyEquivalent_(
            "Quit",
            objc.selector(delegate.onQuit_, signature=b"v@:@"), "q")
    quit_item.setTarget_(delegate)
    menu.addItem_(quit_item)

    status_item.setMenu_(menu)
    delegate._status_item = status_item

    win.makeKeyAndOrderFront_(None)


# ── entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()
