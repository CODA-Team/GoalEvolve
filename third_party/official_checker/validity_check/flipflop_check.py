import re
import sys


# ASAP7 standard-cell OUTPUT pin names. A net connected to one of these pins is
# DRIVEN by that cell; every other pin is treated as a cell input (fanin).
#   Y           - combinational logic output
#   Q, QN       - sequential (flop/latch) true / inverted output
#   Z, ZN       - tri-state / buffered output (defensive; not seen in ASAP7 here)
#   CO, CON     - adder carry-out, true / negated
#   S,  SN      - adder sum, true / negated
#   H,  L       - TIEHI / TIELO constant outputs
#   OUT         - generic output (defensive)
#   GCLK        - clock-gate output (SEQ cells)
OUTPUT_PINS = frozenset({
    "Y", "Q", "QN", "Z", "ZN", "CO", "CON", "S", "SN", "H", "L", "GCLK", "OUT",
})


def parse_dffs(netlist_path):
    with open(netlist_path, "r") as f:
        content = f.read()

    instances = {}

    # Match a DFF instantiation line. The instance name may be an escaped
    # identifier (\foo.bar[0]) or a plain synthesizer name (_450455_) with NO
    # leading backslash, so the backslash is optional. Anchor to line start so
    # the non-greedy name group can't latch onto a substring mid-line.
    pattern = re.compile(
        r'^\s*(DFF\S+)\s+\\?(\S+?)\s*\(([^;]+)\);',
        re.DOTALL | re.MULTILINE
    )

    for match in pattern.finditer(content):
        cell_type = match.group(1)
        inst_name = match.group(2)
        pin_block = match.group(3)

        d_net = None
        q_net = None
        clk_net = None

        # Verilog escaped identifiers end at whitespace: \net[0] )
        # Regular identifiers end at ): net[0])
        # Pattern: optional \, then non-whitespace chars, optional trailing space
        _net = r'\\?(\S+?)\s*\)'

        d_match = re.search(r'\.D\s*\(' + _net, pin_block)
        q_match = re.search(r'\.QN\s*\(' + _net, pin_block)
        if q_match is None:
            q_match = re.search(r'\.Q\s*\(' + _net, pin_block)
        clk_match = re.search(r'\.CLK\s*\(' + _net, pin_block)

        if d_match:
            d_net = d_match.group(1)
        if q_match:
            q_net = q_match.group(1)
        if clk_match:
            clk_net = clk_match.group(1)

        instances[inst_name] = {
            "cell": cell_type,
            "D": d_net,
            "Q": q_net,
            "CLK": clk_net,
        }

    return instances


def parse_net_drivers(netlist_path):
    """Build two maps from the netlist:
      net_drivers: {net_name: (cell_type, inst_name, output_pin)}
                   or {net_name: ("input_port", net_name, "")} for module inputs
      cell_inputs: {inst_name: {input_pin: net_name}}  — for fanin tracing
    """
    with open(netlist_path, "r") as f:
        content = f.read()

    net_drivers = {}
    cell_inputs = {}

    # Module input ports
    input_pattern = re.compile(r'\binput\b[^;]*?(\b\w[\w\[\]]*)\s*;', re.DOTALL)
    for m in input_pattern.finditer(content):
        net = m.group(1).strip()
        net_drivers[net] = ("input_port", net, "")

    # All cell instances
    cell_pattern = re.compile(
        r'(\w\S+)\s+\\?(\S+?)\s*\(([^;]+)\);',
        re.DOTALL
    )

    for m in cell_pattern.finditer(content):
        cell_type = m.group(1)
        inst_name = m.group(2).lstrip("\\")
        pin_block = m.group(3)

        pin_matches = list(re.finditer(r'\.([A-Z][A-Z0-9]*)\s*\(\\?(\S+?)\s*\)', pin_block))
        inst_pin_map = {}
        for pm in pin_matches:
            pin_name = pm.group(1)
            net_name = pm.group(2)
            inst_pin_map[pin_name] = net_name
            # Register output pins as drivers
            if pin_name in OUTPUT_PINS:
                net_drivers[net_name] = (cell_type, inst_name, pin_name)

        cell_inputs[inst_name] = inst_pin_map

    return net_drivers, cell_inputs


def trace_clk_fanin(clk_net, net_drivers, cell_inputs, max_depth=10):
    """Walk back from clk_net through the driver graph to primary inputs.

    Returns a list of strings describing the fanin chain, e.g.:
      clk_i  (input_port)
    or for gated clocks:
      clk_gated  <- AND3x1_ASAP7_75t_R u_gate  [A=nvdla_core_clk, B=en]
        nvdla_core_clk  (input_port)
        en  <- DFFHQNx1_ASAP7_75t_SL u_en_reg  [...]
    """
    visited = set()
    lines = []

    def _trace(net, depth):
        if depth > max_depth or net in visited:
            return
        visited.add(net)
        indent = "  " * depth

        if net not in net_drivers:
            lines.append(f"{indent}{net}  (<undriven/unknown>)")
            return

        cell_type, inst_name, out_pin = net_drivers[net]

        if cell_type == "input_port":
            lines.append(f"{indent}{net}  (input port)")
            return

        # Collect input nets of this cell (everything that isn't an output pin)
        in_pins = cell_inputs.get(inst_name, {})
        fanin_nets = {p: n for p, n in in_pins.items()
                      if p not in OUTPUT_PINS}
        fanin_str = ", ".join(f"{p}={n}" for p, n in sorted(fanin_nets.items()))
        lines.append(f"{indent}{net}  <- {cell_type} ({inst_name})  [{fanin_str}]")

        for p, n in sorted(fanin_nets.items()):
            _trace(n, depth + 1)

    _trace(clk_net, 0)
    return lines


def compare_netlists(netlist1_path, netlist2_path):
    dffs1 = parse_dffs(netlist1_path)
    dffs2 = parse_dffs(netlist2_path)

    print(f"Netlist 1: {netlist1_path}")
    print(f"  DFF count: {len(dffs1)}")
    print(f"Netlist 2: {netlist2_path}")
    print(f"  DFF count: {len(dffs2)}")
    print()

    failed = False

    # Test 1: DFF count check
    print("=" * 50)
    print("Test 1: DFF Count Check")
    print("=" * 50)
    if len(dffs1) != len(dffs2):
        failed = True
        print(f"[FAIL] DFF count mismatch: {len(dffs1)} vs {len(dffs2)}")
        only_in_1 = set(dffs1) - set(dffs2)
        only_in_2 = set(dffs2) - set(dffs1)
        if only_in_1:
            print(f"\n  Instances only in Netlist 1 ({len(only_in_1)}):")
            for name in sorted(only_in_1):
                print(f"    {name}")
        if only_in_2:
            print(f"\n  Instances only in Netlist 2 ({len(only_in_2)}):")
            for name in sorted(only_in_2):
                print(f"    {name}")
    else:
        print(f"[PASS] DFF counts match: {len(dffs1)}")

    # Test 2 operates on the intersection of instances present in both netlists
    common = set(dffs1) & set(dffs2)

    # Test 2: Clock net and clock driver check (with full fanin trace)
    print()
    print("=" * 50)
    print("Test 2: Clock Net and Driver Check")
    print("=" * 50)

    print("  Parsing net drivers for fanin tracing...", flush=True)
    drivers1, cell_inputs1 = parse_net_drivers(netlist1_path)
    drivers2, cell_inputs2 = parse_net_drivers(netlist2_path)

    # Collect unique CLK nets across all matched DFFs for both netlists
    clk_nets1 = {}  # clk_net -> list of instances
    clk_nets2 = {}
    for inst in sorted(common):
        c1 = dffs1[inst]["CLK"]
        c2 = dffs2[inst]["CLK"]
        if c1:
            clk_nets1.setdefault(c1, []).append(inst)
        if c2:
            clk_nets2.setdefault(c2, []).append(inst)

    # Print fanin for every unique clock net in Golden
    print()
    print("  --- Clock Fanin (Golden Netlist) ---")
    for clk_net in sorted(clk_nets1):
        print(f"  CLK net '{clk_net}'  ({len(clk_nets1[clk_net])} DFFs)")
        for line in trace_clk_fanin(clk_net, drivers1, cell_inputs1):
            print(f"    {line}")

    # Print fanin for every unique clock net in Revised
    print()
    print("  --- Clock Fanin (Revised Netlist) ---")
    for clk_net in sorted(clk_nets2):
        print(f"  CLK net '{clk_net}'  ({len(clk_nets2[clk_net])} DFFs)")
        for line in trace_clk_fanin(clk_net, drivers2, cell_inputs2):
            print(f"    {line}")

    # Check for mismatches (net name or immediate driver)
    clk_mismatches = []
    for inst in sorted(common):
        clk1 = dffs1[inst]["CLK"]
        clk2 = dffs2[inst]["CLK"]
        drv1 = drivers1.get(clk1, ("<unknown>", "", "")) if clk1 else ("<no CLK pin>", "", "")
        drv2 = drivers2.get(clk2, ("<unknown>", "", "")) if clk2 else ("<no CLK pin>", "", "")
        drv1_str = f"{drv1[0]} {drv1[1]}" if drv1[0] != "input_port" else "input port"
        drv2_str = f"{drv2[0]} {drv2[1]}" if drv2[0] != "input_port" else "input port"
        if clk1 != clk2 or drv1_str != drv2_str:
            clk_mismatches.append((inst, clk1, drv1_str, clk2, drv2_str))

    print()
    if not clk_mismatches:
        print(f"[PASS] All {len(common)} matched DFF instances have matching clock nets and drivers.")
    else:
        failed = True
        print(f"[FAIL] {len(clk_mismatches)} instance(s) with clock net/driver mismatches:")
        for inst, clk1, drv1, clk2, drv2 in clk_mismatches:
            print(f"\n  Instance: {inst}")
            if clk1 != clk2:
                print(f"    CLK net [Golden]: {clk1}  (driven by: {drv1})")
                print(f"    CLK net [Revised ]: {clk2}  (driven by: {drv2})")
            else:
                print(f"    CLK net          : {clk1}")
                print(f"    CLK driver [Golden]: {drv1}")
                print(f"    CLK driver [Revised ]: {drv2}")

    if failed:
        sys.exit(1)


class _Tee:
    """Write to both a file and the original stream simultaneously."""
    def __init__(self, stream, filepath):
        self._stream = stream
        self._file = open(filepath, "w")

    def write(self, data):
        self._stream.write(data)
        self._file.write(data)

    def flush(self):
        self._stream.flush()
        self._file.flush()

    def close(self):
        self._file.close()


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print("Usage: python3 flipflop_check.py <netlist1.v> <netlist2.v> [logfile.log]")
        sys.exit(1)

    # Derive log path: explicit arg, or auto-generate beside netlist2
    if len(sys.argv) == 4:
        log_path = sys.argv[3]
    else:
        import os
        base = os.path.splitext(os.path.basename(sys.argv[2]))[0]
        log_path = os.path.join(os.path.dirname(sys.argv[2]) or ".", f"{base}_lec.log")

    tee = _Tee(sys.stdout, log_path)
    sys.stdout = tee
    try:
        compare_netlists(sys.argv[1], sys.argv[2])
    finally:
        sys.stdout = tee._stream
        tee.close()
        print(f"\nLog written to: {log_path}")
