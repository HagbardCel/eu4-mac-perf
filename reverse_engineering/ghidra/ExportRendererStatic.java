// Export machine-level static evidence for a fixed RVA list (renderer snapshot).
//@category EU4.RE
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressRange;
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.symbol.FlowTypeOverride;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolTable;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

public class ExportRendererStatic extends GhidraScript {

    private long imageBase;
    private Listing listing;
    private FunctionManager functionManager;
    private SymbolTable symbolTable;

    private static class Interval {
        long start;
        long end;
        Interval(long s, long e) { start = s; end = e; }
    }

    private static String esc(String s) {
        if (s == null) {
            return "null";
        }
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r") + "\"";
    }

    private String rva(Address addr) {
        return String.format("0x%016x", addr.getOffset() - imageBase);
    }

    private long parseRva(String text) {
        String t = text.trim();
        if (t.startsWith("0x") || t.startsWith("0X")) {
            t = t.substring(2);
        }
        return Long.parseUnsignedLong(t, 16);
    }

    private static List<Interval> mergeIntervals(List<Interval> input) {
        if (input.isEmpty()) {
            return input;
        }
        Collections.sort(input, Comparator.comparingLong(i -> i.start));
        List<Interval> out = new ArrayList<>();
        Interval cur = input.get(0);
        for (int i = 1; i < input.size(); i++) {
            Interval next = input.get(i);
            if (next.start <= cur.end) {
                cur.end = Math.max(cur.end, next.end);
            } else {
                out.add(cur);
                cur = next;
            }
        }
        out.add(cur);
        return out;
    }

    private static long unionSize(List<Interval> intervals) {
        long total = 0;
        for (Interval i : mergeIntervals(intervals)) {
            total += (i.end - i.start);
        }
        return total;
    }

    private List<Interval> instructionIntervals(AddressSetView body) {
        List<Interval> intervals = new ArrayList<>();
        InstructionIterator it = listing.getInstructions(body, true);
        while (it.hasNext()) {
            Instruction ins = it.next();
            long start = ins.getMinAddress().getOffset() - imageBase;
            long end = start + ins.getLength();
            intervals.add(new Interval(start, end));
        }
        return intervals;
    }

    private static class PcodeScan {
        boolean usable;
        boolean hasCall;
        boolean hasCallInd;
    }

    private PcodeScan scanPcode(Instruction ins) {
        PcodeScan out = new PcodeScan();
        PcodeOp[] ops;
        try {
            ops = ins.getPcode();
        } catch (Exception e) {
            return out;
        }
        if (ops == null || ops.length == 0) {
            out.usable = true;
            return out;
        }
        out.usable = true;
        for (PcodeOp op : ops) {
            if (op == null) {
                continue;
            }
            int opcode = op.getOpcode();
            if (opcode == PcodeOp.UNIMPLEMENTED) {
                out.usable = false;
                return out;
            }
            if (opcode == PcodeOp.CALL) {
                out.hasCall = true;
            } else if (opcode == PcodeOp.CALLIND) {
                out.hasCallInd = true;
            }
        }
        return out;
    }

    private static String pcodeKind(PcodeScan scan) {
        if (!scan.usable) {
            return "none";
        }
        if (scan.hasCall && scan.hasCallInd) {
            return "both";
        }
        if (scan.hasCallInd) {
            return "CALLIND";
        }
        if (scan.hasCall) {
            return "CALL";
        }
        return "none";
    }

    private static boolean pcodeIsCall(PcodeScan scan) {
        return scan.usable && (scan.hasCall || scan.hasCallInd);
    }

    private static String flowInstructionClass(boolean flowIsCall, boolean flowIsComputed) {
        if (!flowIsCall) {
            return null;
        }
        return flowIsComputed ? "indirect" : "direct";
    }

    private static String flowOverrideName(Instruction ins) {
        FlowTypeOverride override = ins.getFlowOverride();
        if (override == null || override == FlowTypeOverride.NONE) {
            return "NONE";
        }
        return override.name();
    }

    private String flowDestinationsJson(Instruction ins) {
        Address[] flows = ins.getFlows();
        if (flows == null || flows.length == 0) {
            return "[]";
        }
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (Address dest : flows) {
            if (dest == null || !dest.isMemoryAddress()) {
                continue;
            }
            if (!first) {
                sb.append(",");
            }
            first = false;
            sb.append(esc(rva(dest)));
        }
        sb.append("]");
        return sb.toString();
    }

    private void writeMeta(File metaOut) throws Exception {
        String exeSha = currentProgram.getExecutableSHA256();
        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"program_name\": ").append(esc(currentProgram.getName())).append(",\n");
        sb.append("  \"image_base\": ").append(imageBase).append(",\n");
        sb.append("  \"executable_sha256\": ").append(esc(exeSha)).append("\n");
        sb.append("}\n");
        Files.writeString(metaOut.toPath(), sb.toString());
    }

    private String bodyRangesJson(AddressSetView body) {
        List<Interval> intervals = new ArrayList<>();
        for (AddressRange range : body.getAddressRanges()) {
            long start = range.getMinAddress().getOffset() - imageBase;
            long end = range.getMaxAddress().getOffset() + 1 - imageBase;
            intervals.add(new Interval(start, end));
        }
        List<Interval> merged = mergeIntervals(intervals);
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (Interval i : merged) {
            if (!first) {
                sb.append(",");
            }
            first = false;
            sb.append("{\"start_rva\":").append(esc(String.format("0x%016x", i.start)));
            sb.append(",\"end_rva\":").append(esc(String.format("0x%016x", i.end))).append("}");
        }
        sb.append("]");
        return sb.toString();
    }

    private String referencesJson(Instruction ins) {
        Reference[] refs = ins.getReferencesFrom();
        if (refs == null || refs.length == 0) {
            return "[]";
        }
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (Reference ref : refs) {
            if (ref == null) {
                continue;
            }
            if (!first) {
                sb.append(",");
            }
            first = false;
            sb.append("{");
            sb.append("\"ref_type\":").append(esc(ref.getReferenceType().getName()));
            Address to = ref.getToAddress();
            if (to != null && to.isMemoryAddress()) {
                sb.append(",\"target_rva\":").append(esc(rva(to)));
            } else if (to != null) {
                sb.append(",\"target_external\":").append(esc(to.toString()));
            }
            sb.append("}");
        }
        sb.append("]");
        return sb.toString();
    }

    private String bytesHex(Instruction ins) {
        byte[] raw = ins.getBytes();
        if (raw == null || raw.length == 0) {
            return "";
        }
        StringBuilder sb = new StringBuilder();
        for (byte b : raw) {
            sb.append(String.format("%02x", b & 0xff));
        }
        return sb.toString();
    }

    private void failBatchIdentity(String message) throws Exception {
        throw new IllegalStateException("batch identity failure: " + message);
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 3) {
            throw new IllegalArgumentException(
                "usage: ExportRendererStatic.java <rvaListFile> <outputJsonl> <metaJson>");
        }
        File rvaFile = new File(args[0]);
        File outFile = new File(args[1]);
        File metaOut = new File(args[2]);

        imageBase = currentProgram.getImageBase().getOffset();
        listing = currentProgram.getListing();
        functionManager = currentProgram.getFunctionManager();
        symbolTable = currentProgram.getSymbolTable();

        writeMeta(metaOut);

        BufferedWriter writer = new BufferedWriter(new FileWriter(outFile));
        List<String> lines = Files.readAllLines(rvaFile.toPath());
        for (String line : lines) {
            if (line.trim().isEmpty() || line.startsWith("#")) {
                continue;
            }
            long requestedRva = parseRva(line);
            Address entry = currentProgram.getAddressFactory().getDefaultAddressSpace()
                .getAddress(imageBase + requestedRva);
            Function fn = functionManager.getFunctionAt(entry);
            if (fn == null) {
                failBatchIdentity("no function at " + String.format("0x%016x", requestedRva));
            }
            long entryRva = fn.getEntryPoint().getOffset() - imageBase;
            if (entryRva != requestedRva) {
                failBatchIdentity("entry mismatch at " + String.format("0x%016x", requestedRva));
            }
            try {
                writer.write(exportFunction(fn, requestedRva));
                writer.flush();
            } catch (Exception e) {
                writer.write(failedRecord(requestedRva, fn, e.getMessage()));
                writer.flush();
            }
        }
        writer.close();
        println("ExportRendererStatic wrote " + outFile.getAbsolutePath());
    }

    private String failedRecord(long rva, Function fn, String error) {
        StringBuilder sb = new StringBuilder();
        sb.append("{");
        sb.append("\"entry_rva\":").append(esc(String.format("0x%016x", rva)));
        sb.append(",\"status\":").append(esc("failed"));
        sb.append(",\"error\":").append(esc(error));
        if (fn != null) {
            Symbol sym = fn.getSymbol();
            sb.append(",\"name_mangled\":").append(esc(sym != null ? sym.getName() : fn.getName()));
        }
        sb.append("}\n");
        return sb.toString();
    }

    private String exportFunction(Function fn, long requestedRva) throws Exception {
        AddressSetView body = fn.getBody();
        List<Interval> insnIntervals = instructionIntervals(body);
        List<Interval> mergedInsn = mergeIntervals(new ArrayList<>(insnIntervals));
        int insCount = 0;
        long decodedBytes = unionSize(insnIntervals);
        StringBuilder instructions = new StringBuilder("[");
        boolean firstIns = true;
        InstructionIterator it = listing.getInstructions(body, true);
        while (it.hasNext()) {
            Instruction ins = it.next();
            insCount++;
            if (!firstIns) {
                instructions.append(",");
            }
            firstIns = false;
            PcodeScan scan = scanPcode(ins);
            boolean flowIsCall = ins.getFlowType().isCall();
            boolean flowIsComputed = ins.getFlowType().isComputed();
            boolean pcodeCall = pcodeIsCall(scan);
            boolean censusCall = pcodeCall || flowIsCall;
            String mnemonic = ins.getMnemonicString();
            boolean machineCall = mnemonic != null
                && (mnemonic.equalsIgnoreCase("CALL") || mnemonic.equalsIgnoreCase("CALLQ"));
            String flowClass = flowInstructionClass(flowIsCall, flowIsComputed);
            Address fallThrough = ins.getFallThrough();

            instructions.append("{");
            instructions.append("\"instruction_rva\":").append(esc(rva(ins.getMinAddress())));
            instructions.append(",\"length\":").append(ins.getLength());
            instructions.append(",\"bytes_hex\":").append(esc(bytesHex(ins)));
            instructions.append(",\"mnemonic\":").append(esc(mnemonic));
            instructions.append(",\"operands\":").append(esc(ins.toString()));
            instructions.append(",\"flow_type_is_call\":").append(flowIsCall);
            instructions.append(",\"flow_is_computed\":").append(flowIsComputed);
            instructions.append(",\"flow_override\":").append(esc(flowOverrideName(ins)));
            instructions.append(",\"fallthrough_overridden\":").append(ins.isFallThroughOverridden());
            if (fallThrough != null && fallThrough.isMemoryAddress()) {
                instructions.append(",\"fallthrough_rva\":").append(esc(rva(fallThrough)));
            }
            instructions.append(",\"flow_destinations_rvas\":").append(flowDestinationsJson(ins));
            instructions.append(",\"pcode_kind\":").append(esc(pcodeKind(scan)));
            instructions.append(",\"pcode_is_call\":").append(pcodeCall);
            instructions.append(",\"census_call\":").append(censusCall);
            if (flowClass != null) {
                instructions.append(",\"flow_instruction_class\":").append(esc(flowClass));
            }
            instructions.append(",\"machine_call\":").append(machineCall);
            instructions.append(",\"references_from\":").append(referencesJson(ins));
            instructions.append("}");
        }
        instructions.append("]");

        Symbol sym = fn.getSymbol();
        String mangled = sym != null ? sym.getName() : fn.getName();
        String demangled = sym != null ? sym.getName(true) : fn.getName();
        String proto = fn.getSignature().getPrototypeString();

        StringBuilder sb = new StringBuilder();
        sb.append("{");
        sb.append("\"entry_rva\":").append(esc(String.format("0x%016x", requestedRva)));
        sb.append(",\"status\":").append(esc("success"));
        sb.append(",\"name_mangled\":").append(esc(mangled));
        sb.append(",\"name_demangled\":").append(esc(demangled));
        sb.append(",\"prototype\":").append(esc(proto));
        sb.append(",\"instruction_count\":").append(insCount);
        sb.append(",\"decoded_instruction_bytes\":").append(decodedBytes);
        sb.append(",\"body_ranges\":").append(bodyRangesJson(body));
        sb.append(",\"decoded_instruction_ranges\":").append(rangesJsonFromMerged(mergedInsn));
        sb.append(",\"instructions\":").append(instructions);
        sb.append("}\n");
        return sb.toString();
    }

    private String rangesJsonFromMerged(List<Interval> merged) {
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (Interval i : merged) {
            if (!first) {
                sb.append(",");
            }
            first = false;
            sb.append("{\"start_rva\":").append(esc(String.format("0x%016x", i.start)));
            sb.append(",\"end_rva\":").append(esc(String.format("0x%016x", i.end))).append("}");
        }
        sb.append("]");
        return sb.toString();
    }
}
