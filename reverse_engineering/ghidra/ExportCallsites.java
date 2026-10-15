// Read-only census of decoded call instructions in executable memory.
//@category EU4.RE
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.symbol.Reference;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

public class ExportCallsites extends GhidraScript {

    private static final int FAILURE_SAMPLE_CAP = 32;

    private long imageBase;
    private Listing listing;
    private FunctionManager functionManager;

    private long totalInstructionsScanned;
    private int pcodeDecodeFailures;
    private int pcodeUnimplementedCount;
    private final List<String> pcodeFailureSample = new ArrayList<>();

    private static String esc(String s) {
        if (s == null) {
            return "null";
        }
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\"";
    }

    private String rva(Address addr) {
        return String.format("0x%016x", addr.getOffset() - imageBase);
    }

    private void recordPcodeFailure(String callsiteRva) {
        pcodeDecodeFailures++;
        if (pcodeFailureSample.size() < FAILURE_SAMPLE_CAP) {
            pcodeFailureSample.add(callsiteRva);
        }
    }

    private static class PcodeScan {
        boolean usable;
        boolean hasCall;
        boolean hasCallInd;
        boolean unimplemented;
    }

    private PcodeScan scanPcode(Instruction ins, String insnRva) {
        PcodeScan out = new PcodeScan();
        PcodeOp[] ops;
        try {
            ops = ins.getPcode();
        } catch (Exception e) {
            recordPcodeFailure(insnRva);
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
                out.unimplemented = true;
                pcodeUnimplementedCount++;
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

    private static String pcodeInstructionClass(PcodeScan scan) {
        if (!scan.usable) {
            return null;
        }
        if (scan.hasCall && scan.hasCallInd) {
            return "ambiguous";
        }
        if (scan.hasCallInd) {
            return "indirect";
        }
        if (scan.hasCall) {
            return "direct";
        }
        return null;
    }

    private static String flowInstructionClass(boolean flowIsCall, boolean flowIsComputed) {
        if (!flowIsCall) {
            return null;
        }
        return flowIsComputed ? "indirect" : "direct";
    }

    private static String instructionClass(PcodeScan scan, boolean flowIsCall, boolean flowIsComputed) {
        if (!scan.usable) {
            if (flowIsCall) {
                return flowIsComputed ? "indirect" : "direct";
            }
            return "ambiguous";
        }
        String pcodeClass = pcodeInstructionClass(scan);
        if (pcodeClass != null) {
            return pcodeClass;
        }
        if (flowIsCall) {
            return flowIsComputed ? "indirect" : "direct";
        }
        return "ambiguous";
    }

    private static boolean classificationConflict(
            PcodeScan scan, boolean flowIsCall, boolean flowIsComputed, boolean pcodeCall) {
        if (!scan.usable || !pcodeCall || !flowIsCall) {
            return false;
        }
        String pcodeClass = pcodeInstructionClass(scan);
        String flowClass = flowInstructionClass(flowIsCall, flowIsComputed);
        return pcodeClass != null && flowClass != null && !pcodeClass.equals(flowClass);
    }

    private static String classifierStatus(PcodeScan scan, boolean flowIsCall, boolean pcodeIsCall) {
        if (!scan.usable) {
            return "pcode_unavailable";
        }
        if (pcodeIsCall != flowIsCall) {
            return "disagreement";
        }
        return "agreement";
    }

    private int callReferenceCount(Instruction ins) {
        Reference[] refs = ins.getReferencesFrom();
        if (refs == null) {
            return 0;
        }
        int n = 0;
        for (Reference ref : refs) {
            if (ref != null && ref.getReferenceType().isCall()) {
                n++;
            }
        }
        return n;
    }

    private String callerRva(Instruction ins) {
        Function fn = functionManager.getFunctionContaining(ins.getMinAddress());
        if (fn == null) {
            return null;
        }
        return rva(fn.getEntryPoint());
    }

    private static class CallsiteRow implements Comparable<CallsiteRow> {
        String line;
        String callsiteRva;

        CallsiteRow(String callsiteRva, String line) {
            this.callsiteRva = callsiteRva;
            this.line = line;
        }

        @Override
        public int compareTo(CallsiteRow other) {
            return this.callsiteRva.compareTo(other.callsiteRva);
        }
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            throw new IllegalArgumentException("usage: ExportCallsites.java <stagingDir>");
        }
        File stagingDir = new File(args[0]);
        stagingDir.mkdirs();
        File callsitesOut = new File(stagingDir, "callsites-v1.jsonl");
        File metaOut = new File(stagingDir, "scan-meta.json");

        imageBase = currentProgram.getImageBase().getOffset();
        listing = currentProgram.getListing();
        functionManager = currentProgram.getFunctionManager();

        List<CallsiteRow> rows = new ArrayList<>();
        Set<String> seenRvas = new HashSet<>();

        for (MemoryBlock block : currentProgram.getMemory().getBlocks()) {
            if (!block.isExecute()) {
                continue;
            }
            AddressSet range = new AddressSet(block.getStart(), block.getEnd());
            InstructionIterator it = listing.getInstructions(range, true);
            while (it.hasNext()) {
                Instruction ins = it.next();
                totalInstructionsScanned++;
                String insnRva = rva(ins.getMinAddress());
                PcodeScan scan = scanPcode(ins, insnRva);
                boolean flowIsCall = ins.getFlowType().isCall();
                boolean flowIsComputed = ins.getFlowType().isComputed();
                boolean pcodeCall = pcodeIsCall(scan);
                if (!pcodeCall && !flowIsCall) {
                    continue;
                }
                if (seenRvas.contains(insnRva)) {
                    throw new IllegalStateException("duplicate callsite RVA: " + insnRva);
                }
                seenRvas.add(insnRva);
                String pcodeKindStr = pcodeKind(scan);
                String instrClass = instructionClass(scan, flowIsCall, flowIsComputed);
                String status = classifierStatus(scan, flowIsCall, pcodeCall);
                String flowClass = flowInstructionClass(flowIsCall, flowIsComputed);
                boolean classConflict = classificationConflict(scan, flowIsCall, flowIsComputed, pcodeCall);
                String caller = callerRva(ins);
                int refCount = callReferenceCount(ins);
                String mnemonic = ins.getMnemonicString();

                StringBuilder line = new StringBuilder();
                line.append("{");
                line.append("\"callsite_rva\":").append(esc(insnRva));
                line.append(",\"caller_rva\":").append(caller == null ? "null" : esc(caller));
                line.append(",\"mnemonic\":").append(esc(mnemonic));
                line.append(",\"pcode_kind\":").append(esc(pcodeKindStr));
                line.append(",\"flow_type_is_call\":").append(flowIsCall);
                line.append(",\"pcode_is_call\":").append(pcodeCall);
                line.append(",\"flow_instruction_class\":").append(
                    flowClass == null ? "null" : esc(flowClass));
                line.append(",\"classification_conflict\":").append(classConflict);
                line.append(",\"classifier_status\":").append(esc(status));
                line.append(",\"instruction_class\":").append(esc(instrClass));
                line.append(",\"call_reference_count\":").append(refCount);
                line.append("}");
                rows.add(new CallsiteRow(insnRva, line.toString()));
            }
        }

        Collections.sort(rows);
        try (BufferedWriter w = new BufferedWriter(new FileWriter(callsitesOut))) {
            for (CallsiteRow row : rows) {
                w.write(row.line);
                w.write("\n");
            }
        }

        String exeSha = currentProgram.getExecutableSHA256();
        StringBuilder meta = new StringBuilder();
        meta.append("{\n");
        meta.append("  \"total_instructions_scanned\": ").append(totalInstructionsScanned).append(",\n");
        meta.append("  \"total_callsites\": ").append(rows.size()).append(",\n");
        meta.append("  \"pcode_decode_failures\": ").append(pcodeDecodeFailures).append(",\n");
        meta.append("  \"pcode_unimplemented_count\": ").append(pcodeUnimplementedCount).append(",\n");
        meta.append("  \"pcode_failure_sample_rvas\": [");
        for (int i = 0; i < pcodeFailureSample.size(); i++) {
            if (i > 0) {
                meta.append(", ");
            }
            meta.append(esc(pcodeFailureSample.get(i)));
        }
        meta.append("],\n");
        meta.append("  \"executable_sha256\": ");
        meta.append(exeSha == null ? "null" : esc(exeSha.toLowerCase()));
        meta.append(",\n");
        meta.append("  \"program_name\": ").append(esc(currentProgram.getName())).append(",\n");
        meta.append("  \"image_base\": ").append(imageBase).append(",\n");
        meta.append("  \"exporter_status\": ").append(esc("ok")).append("\n");
        meta.append("}\n");
        Files.writeString(metaOut.toPath(), meta.toString(), StandardCharsets.UTF_8);
        println("ExportCallsites complete: " + callsitesOut.getAbsolutePath() + " (" + rows.size() + " callsites)");
    }
}
