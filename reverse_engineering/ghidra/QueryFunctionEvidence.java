// Bounded function evidence queries (read-only).
//@category EU4.RE
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.mem.MemoryAccessException;
import ghidra.program.model.symbol.RefType;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceManager;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.data.DataType;
import ghidra.program.model.data.Structure;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.pcode.HighFunction;
import ghidra.program.model.pcode.HighVariable;
import ghidra.program.model.pcode.Varnode;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.PcodeOpAST;
import ghidra.program.model.pcode.SequenceNumber;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;

public class QueryFunctionEvidence extends GhidraScript {

    private static String esc(String s) {
        if (s == null) return "null";
        StringBuilder out = new StringBuilder();
        out.append('\"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '\\': out.append("\\\\"); break;
                case '\"': out.append("\\\""); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
                    else out.append(c);
            }
        }
        out.append('\"');
        return out.toString();
    }

    private String seqRvaJson(long imageBase, SequenceNumber seq) {
        if (seq == null || seq.getTarget() == null) return "null";
        long off = seq.getTarget().getOffset() - imageBase;
        return esc(rvaHex(off));
    }

    private long parseRva(String text) {
        String t = text.trim();
        if (t.startsWith("0x") || t.startsWith("0X")) t = t.substring(2);
        return Long.parseUnsignedLong(t, 16);
    }

    private String rvaHex(long rva) {
        return String.format("0x%016x", rva);
    }

    private Address rvaToAddr(long imageBase, long rva) {
        return currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(imageBase + rva);
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 4) {
            throw new IllegalArgumentException(
                "usage: QueryFunctionEvidence.java <rvaListFile> <operation> <outputJsonl> <maxInsnsPerFn>");
        }
        File rvaFile = new File(args[0]);
        String operation = args[1];
        File outFile = new File(args[2]);
        int maxInsns = Integer.parseInt(args[3]);
        long imageBase = currentProgram.getImageBase().getOffset();
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager refMgr = currentProgram.getReferenceManager();

        BufferedWriter writer = new BufferedWriter(new FileWriter(outFile));
        List<String> rvas = Files.readAllLines(rvaFile.toPath());
        for (String line : rvas) {
            if (line.trim().isEmpty() || line.startsWith("#")) continue;
            long rva = parseRva(line);
            Address entry = rvaToAddr(imageBase, rva);
            Function fn = fm.getFunctionAt(entry);
            if (fn == null) fn = fm.getFunctionContaining(entry);
            if (fn == null) {
                writer.write("{\"entry_rva\":" + esc(rvaHex(rva)) + ",\"operation\":" + esc(operation)
                    + ",\"status\":\"unresolved\",\"status_detail\":\"no_function_at_rva\"}\n");
                continue;
            }
            StringBuilder sb = new StringBuilder();
            sb.append("{\"entry_rva\":").append(esc(rvaHex(rva)));
            sb.append(",\"operation\":").append(esc(operation));
            sb.append(",\"facts\":[");
            List<String> facts = new ArrayList<>();
            List<String> interpretations = new ArrayList<>();

            switch (operation) {
                case "disassembly":
                    emitDisassembly(fn, maxInsns, facts);
                    sb.append(joinJson(facts));
                    sb.append("],\"interpretations\":[],\"status\":\"complete_for_requested_scope\"");
                    break;
                case "references":
                    emitReferences(fn, refMgr, facts, interpretations);
                    sb.append(joinJson(facts));
                    sb.append("],\"interpretations\":").append("[").append(joinJson(interpretations)).append("]");
                    sb.append(",\"status\":\"partial\"");
                    break;
                case "signature":
                    emitSignature(fn, facts, interpretations);
                    sb.append(joinJson(facts));
                    sb.append("],\"interpretations\":").append("[").append(joinJson(interpretations)).append("]");
                    sb.append(",\"status\":\"complete_for_requested_scope\"");
                    break;
                case "type_layout":
                    emitTypeLayout(fn, facts, interpretations);
                    sb.append(joinJson(facts));
                    sb.append("],\"interpretations\":").append("[").append(joinJson(interpretations)).append("]");
                    sb.append(",\"status\":\"partial\"");
                    break;
                case "data_accesses":
                    emitDataAccesses(fn, maxInsns, facts, interpretations);
                    sb.append(joinJson(facts));
                    sb.append("],\"interpretations\":").append("[").append(joinJson(interpretations)).append("]");
                    sb.append(",\"status\":\"partial\"");
                    break;
                case "indirect_dispatch":
                    emitIndirectDispatch(fn, maxInsns, facts, interpretations);
                    sb.append(joinJson(facts));
                    sb.append("],\"interpretations\":").append("[").append(joinJson(interpretations)).append("]");
                    sb.append(",\"status\":\"partial\"");
                    break;
                default:
                    sb.append("],\"interpretations\":[],\"status\":\"unsupported\",\"status_detail\":\"unknown_operation\"");
            }
            sb.append("}\n");
            writer.write(sb.toString());
        }
        writer.close();
        println("QueryFunctionEvidence wrote " + outFile.getAbsolutePath());
    }

    private String joinJson(List<String> items) {
        StringBuilder j = new StringBuilder();
        for (int i = 0; i < items.size(); i++) {
            if (i > 0) j.append(",");
            j.append(items.get(i));
        }
        return j.toString();
    }

    private void emitDisassembly(Function fn, int maxInsns, List<String> facts) {
        InstructionIterator it = currentProgram.getListing().getInstructions(fn.getBody(), true);
        int n = 0;
        while (it.hasNext() && n < maxInsns) {
            Instruction ins = it.next();
            long rva = ins.getAddress().getOffset() - currentProgram.getImageBase().getOffset();
            facts.add("{\"kind\":\"instruction\",\"instruction_rva\":" + esc(rvaHex(rva))
                + ",\"mnemonic\":" + esc(ins.getMnemonicString())
                + ",\"operands\":" + esc(ins.getDefaultOperandRepresentation(0))
                + ",\"provenance\":\"machine_instruction\"}");
            n++;
        }
    }

    private void emitReferences(Function fn, ReferenceManager refMgr, List<String> facts, List<String> interpretations) {
        long imageBase = currentProgram.getImageBase().getOffset();
        java.util.HashSet<String> seen = new java.util.HashSet<>();
        int c = 0;
        InstructionIterator it = currentProgram.getListing().getInstructions(fn.getBody(), true);
        while (it.hasNext() && c < 200) {
            Instruction ins = it.next();
            Reference[] refs = ins.getReferencesFrom();
            if (refs != null) {
                for (Reference ref : refs) {
                    if (c >= 200) break;
                    String key = ref.getFromAddress() + "->" + ref.getToAddress();
                    if (seen.add(key)) {
                        facts.add(refJson(ref, "outgoing"));
                        c++;
                    }
                }
            }
        }
        Reference[] incoming = refMgr.getReferencesTo(fn.getEntryPoint());
        c = 0;
        if (incoming != null) {
            for (Reference ref : incoming) {
                if (c >= 200) break;
                facts.add(refJson(ref, "incoming"));
                c++;
            }
        }
        interpretations.add("{\"note\":\"reference_list_may_be_incomplete\",\"provenance\":\"ghidra_reference\"}");
    }

    private String refJson(Reference ref, String direction) {
        long fromRva = ref.getFromAddress().getOffset() - currentProgram.getImageBase().getOffset();
        long toRva = ref.getToAddress().getOffset() - currentProgram.getImageBase().getOffset();
        return "{\"kind\":\"reference\",\"direction\":" + esc(direction)
            + ",\"from_rva\":" + esc(rvaHex(fromRva))
            + ",\"to_rva\":" + esc(rvaHex(toRva))
            + ",\"ref_type\":" + esc(ref.getReferenceType().getName())
            + ",\"provenance\":\"ghidra_reference\"}";
    }

    private void emitSignature(Function fn, List<String> facts, List<String> interpretations) {
        facts.add("{\"kind\":\"signature\",\"name\":" + esc(fn.getName())
            + ",\"calling_convention\":" + esc(fn.getCallingConventionName())
            + ",\"return_type\":" + esc(fn.getReturnType().getName())
            + ",\"parameter_count\":" + fn.getParameterCount()
            + ",\"provenance\":\"ghidra_type_inference\"}");
        if (fn.getSignatureSource() == SourceType.DEFAULT) {
            interpretations.add("{\"note\":\"signature_may_be_default_inferred\",\"provenance\":\"ghidra_type_inference\"}");
        }
    }

    private void emitTypeLayout(Function fn, List<String> facts, List<String> interpretations) {
        DataType dt = fn.getReturnType();
        if (dt != null) {
            facts.add("{\"kind\":\"return_type\",\"type\":" + esc(dt.getName())
                + ",\"size\":" + dt.getLength()
                + ",\"provenance\":\"ghidra_type_inference\"}");
        }
        if (dt instanceof Structure) {
            Structure st = (Structure) dt;
            for (int i = 0; i < st.getNumComponents() && i < 64; i++) {
                facts.add("{\"kind\":\"structure_component\",\"offset\":" + st.getComponent(i).getOffset()
                    + ",\"name\":" + esc(st.getComponent(i).getFieldName())
                    + ",\"size\":" + st.getComponent(i).getLength()
                    + ",\"provenance\":\"ghidra_type_inference\"}");
            }
        }
        for (int p = 0; p < fn.getParameterCount() && p < 32; p++) {
            DataType pt = fn.getParameter(p).getDataType();
            facts.add("{\"kind\":\"parameter_type\",\"index\":" + p
                + ",\"type\":" + esc(pt.getName())
                + ",\"provenance\":\"ghidra_type_inference\"}");
        }
        interpretations.add("{\"note\":\"class_identity_may_be_unknown\",\"provenance\":\"ghidra_type_inference\"}");
    }

    private void emitDataAccesses(Function fn, int maxInsns, List<String> facts, List<String> interpretations) {
        boolean any = false;
        InstructionIterator it = currentProgram.getListing().getInstructions(fn.getBody(), true);
        int n = 0;
        while (it.hasNext() && n < maxInsns) {
            Instruction ins = it.next();
            String rep = ins.toString();
            if (rep.contains("[") && (rep.contains("mov") || rep.contains("lea") || rep.contains("cmp"))) {
                long rva = ins.getAddress().getOffset() - currentProgram.getImageBase().getOffset();
                facts.add("{\"kind\":\"memory_access\",\"instruction_rva\":" + esc(rvaHex(rva))
                    + ",\"disassembly\":" + esc(rep)
                    + ",\"operation\":\"unknown_read_or_write\""
                    + ",\"provenance\":\"machine_instruction\"}");
                any = true;
            }
            n++;
        }
        long imageBase = currentProgram.getImageBase().getOffset();
        try {
            DecompInterface decomp = new DecompInterface();
            decomp.openProgram(currentProgram);
            DecompileResults res = decomp.decompileFunction(fn, 60, monitor);
            if (res != null && res.decompileCompleted()) {
                HighFunction hf = res.getHighFunction();
                if (hf != null) {
                    Iterator<PcodeOpAST> pcodeIt = hf.getPcodeOps();
                    while (pcodeIt.hasNext()) {
                        PcodeOpAST op = pcodeIt.next();
                        if (op.getOpcode() == PcodeOp.LOAD || op.getOpcode() == PcodeOp.STORE) {
                            facts.add("{\"kind\":\"pcode_mem_op\",\"opcode\":" + esc(op.getMnemonic())
                                + ",\"seq\":" + seqRvaJson(imageBase, op.getSeqnum())
                                + ",\"provenance\":\"ghidra_pcode\"}");
                            any = true;
                        }
                    }
                }
            }
        } catch (Exception e) {
            interpretations.add("{\"note\":\"pcode_recovery_failed\",\"detail\":" + esc(e.getMessage()) + "}");
        }
        if (!any) {
            interpretations.add("{\"note\":\"no_accesses_identified_in_requested_scope\",\"provenance\":\"machine_instruction\"}");
        } else {
            interpretations.add("{\"note\":\"object_identity_and_field_meaning_uncertain\",\"provenance\":\"ghidra_type_inference\"}");
        }
    }

    private void emitIndirectDispatch(Function fn, int maxInsns, List<String> facts, List<String> interpretations) {
        InstructionIterator it = currentProgram.getListing().getInstructions(fn.getBody(), true);
        int n = 0;
        while (it.hasNext() && n < maxInsns) {
            Instruction ins = it.next();
            String flow = ins.getFlowType().toString();
            if (flow.toLowerCase().contains("indirect") || ins.getMnemonicString().equals("CALL")) {
                long rva = ins.getAddress().getOffset() - currentProgram.getImageBase().getOffset();
                facts.add("{\"kind\":\"control_flow\",\"instruction_rva\":" + esc(rvaHex(rva))
                    + ",\"mnemonic\":" + esc(ins.getMnemonicString())
                    + ",\"flow\":" + esc(flow)
                    + ",\"provenance\":\"machine_instruction\"}");
            }
            n++;
        }
        interpretations.add("{\"note\":\"complete_dispatch_target_set_not_guaranteed\",\"provenance\":\"ghidra_reference\"}");
    }
}
