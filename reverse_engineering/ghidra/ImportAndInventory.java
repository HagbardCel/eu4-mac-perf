// Export function inventory, call edges, and E/D/F coverage.
//@category EU4.RE
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.ExternalLocation;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.RefType;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolTable;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public class ImportAndInventory extends GhidraScript {

    private long imageBase;
    private Listing listing;
    private FunctionManager functionManager;
    private SymbolTable symbolTable;

    private static String esc(String s) {
        if (s == null) {
            return "null";
        }
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\"";
    }

    private String rva(Address addr) {
        return String.format("0x%016x", addr.getOffset() - imageBase);
    }

    private static class Interval {
        long start;
        long end;
        Interval(long s, long e) { start = s; end = e; }
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

    private String rangesJson(List<Interval> merged) {
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

    private String functionKind(Function fn) {
        if (fn.isExternal()) {
            return "external";
        }
        if (fn.isThunk()) {
            return "thunk";
        }
        return "internal";
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            throw new IllegalArgumentException("usage: ImportAndInventory.java <inventoryDir>");
        }
        File invDir = new File(args[0]);
        invDir.mkdirs();
        File functionsOut = new File(invDir, "functions.jsonl");
        File edgesOut = new File(invDir, "call_edges.jsonl");
        File coverageOut = new File(invDir, "coverage.json");

        imageBase = currentProgram.getImageBase().getOffset();
        listing = currentProgram.getListing();
        functionManager = currentProgram.getFunctionManager();
        symbolTable = currentProgram.getSymbolTable();

        List<Interval> decodedAll = new ArrayList<>();
        List<Interval> internalIntervals = new ArrayList<>();
        List<Interval> thunkIntervals = new ArrayList<>();
        long executableSectionBytes = 0;
        Map<String, Long> sectionBytes = new HashMap<>();

        for (MemoryBlock block : currentProgram.getMemory().getBlocks()) {
            if (!block.isExecute()) {
                continue;
            }
            String name = block.getName();
            sectionBytes.put(name, sectionBytes.getOrDefault(name, 0L) + block.getSize());
            executableSectionBytes += block.getSize();
            Address start = block.getStart();
            Address end = block.getEnd();
            AddressSet range = new AddressSet(start, end);
            InstructionIterator it = listing.getInstructions(range, true);
            while (it.hasNext()) {
                Instruction ins = it.next();
                long s = ins.getMinAddress().getOffset() - imageBase;
                long e = s + ins.getLength();
                decodedAll.add(new Interval(s, e));
            }
        }

        BufferedWriter fnWriter = new BufferedWriter(new FileWriter(functionsOut));
        BufferedWriter edgeWriter = new BufferedWriter(new FileWriter(edgesOut));
        Set<String> edgeKeys = new HashSet<>();

        FunctionIterator fit = functionManager.getFunctions(true);
        while (fit.hasNext()) {
            Function fn = fit.next();
            String kind = functionKind(fn);
            Address entry = fn.getEntryPoint();
            List<Interval> fnIntervals = instructionIntervals(fn.getBody());
            List<Interval> mergedFn = mergeIntervals(fnIntervals);
            long fnBytes = unionSize(fnIntervals);
            int insCount = 0;
            InstructionIterator countIt = listing.getInstructions(fn.getBody(), true);
            while (countIt.hasNext()) {
                countIt.next();
                insCount++;
            }
            if ("internal".equals(kind)) {
                internalIntervals.addAll(fnIntervals);
            } else if ("thunk".equals(kind)) {
                thunkIntervals.addAll(fnIntervals);
            }
            Symbol sym = fn.getSymbol();
            String mangled = sym != null ? sym.getName() : fn.getName();
            String demangled = sym != null ? sym.getName(true) : fn.getName();
            String namespace = sym != null && sym.getParentNamespace() != null
                ? sym.getParentNamespace().getName() : "";
            StringBuilder line = new StringBuilder();
            line.append("{");
            line.append("\"entry_rva\":").append(esc(rva(entry)));
            line.append(",\"image_base\":").append(imageBase);
            line.append(",\"kind\":").append(esc(kind));
            line.append(",\"name_mangled\":").append(esc(mangled));
            line.append(",\"name_demangled\":").append(esc(demangled));
            line.append(",\"namespace\":").append(esc(namespace));
            line.append(",\"instruction_count\":").append(insCount);
            line.append(",\"decoded_instruction_bytes\":").append(fnBytes);
            line.append(",\"instruction_ranges\":").append(rangesJson(mergedFn));
            line.append("}\n");
            fnWriter.write(line.toString());
            scanCalls(fn, edgeWriter, edgeKeys);
        }
        fnWriter.close();
        edgeWriter.close();

        long D = unionSize(decodedAll);
        long F_internal = unionSize(internalIntervals);
        long F_thunk = unionSize(thunkIntervals);
        List<Interval> allFn = new ArrayList<>();
        allFn.addAll(internalIntervals);
        allFn.addAll(thunkIntervals);
        long F_all = unionSize(allFn);

        StringBuilder cov = new StringBuilder();
        cov.append("{\n");
        cov.append("  \"image_base\": ").append(imageBase).append(",\n");
        cov.append("  \"E_executable_section_bytes\": ").append(executableSectionBytes).append(",\n");
        cov.append("  \"executable_sections\": {");
        boolean firstSec = true;
        for (Map.Entry<String, Long> e : sectionBytes.entrySet()) {
            if (!firstSec) cov.append(",");
            firstSec = false;
            cov.append("\n    ").append(esc(e.getKey())).append(": ").append(e.getValue());
        }
        cov.append("\n  },\n");
        cov.append("  \"D_decoded_instruction_bytes\": ").append(D).append(",\n");
        cov.append("  \"F_internal_bytes\": ").append(F_internal).append(",\n");
        cov.append("  \"F_thunk_bytes\": ").append(F_thunk).append(",\n");
        cov.append("  \"F_all_bytes\": ").append(F_all).append(",\n");
        cov.append("  \"C_section\": ").append(executableSectionBytes == 0 ? 0 : (double) F_all / executableSectionBytes).append(",\n");
        cov.append("  \"C_decoded\": ").append(D == 0 ? 0 : (double) F_all / D).append("\n");
        cov.append("}\n");
        Files.writeString(coverageOut.toPath(), cov.toString());
        println("ImportAndInventory complete: " + functionsOut.getAbsolutePath());
    }

    private void scanCalls(Function fn, BufferedWriter edgeWriter, Set<String> edgeKeys) throws Exception {
        String callerRva = rva(fn.getEntryPoint());
        InstructionIterator it = listing.getInstructions(fn.getBody(), true);
        while (it.hasNext()) {
            Instruction ins = it.next();
            if (!ins.getFlowType().isCall()) {
                continue;
            }
            Reference[] refs = ins.getReferencesFrom();
            String callsite = rva(ins.getMinAddress());
            if (refs == null || refs.length == 0) {
                if (ins.getFlowType().isIndirect()) {
                    writeEdge(edgeWriter, edgeKeys, callsite, callerRva, null, null, null, "unresolved_indirect");
                }
                continue;
            }
            for (Reference ref : refs) {
                if (!ref.getReferenceType().isCall()) {
                    continue;
                }
                classifyCall(edgeWriter, edgeKeys, callsite, callerRva, ref);
            }
        }
    }

    private void classifyCall(BufferedWriter edgeWriter, Set<String> edgeKeys, String callsite,
            String callerRva, Reference ref) throws Exception {
        Address to = ref.getToAddress();
        if (to == null) {
            writeEdge(edgeWriter, edgeKeys, callsite, callerRva, null, null, null, "unresolved_other");
            return;
        }
        Function targetFn = functionManager.getFunctionAt(to);
        if (targetFn == null) {
            targetFn = functionManager.getFunctionContaining(to);
        }
        if (targetFn != null) {
            if (targetFn.isThunk()) {
                Function ultimate = targetFn.getThunkedFunction(true);
                String calleeInternal = null;
                String calleeExternal = null;
                String ultimateExternal = null;
                if (ultimate != null) {
                    if (ultimate.isExternal()) {
                        ultimateExternal = externalName(ultimate.getEntryPoint());
                        calleeExternal = ultimateExternal;
                    } else {
                        calleeInternal = rva(ultimate.getEntryPoint());
                    }
                } else {
                    calleeInternal = rva(targetFn.getEntryPoint());
                }
                writeEdge(edgeWriter, edgeKeys, callsite, callerRva, calleeInternal, calleeExternal, ultimateExternal, "thunk");
                return;
            }
            if (targetFn.isExternal()) {
                String sym = externalName(to);
                writeEdge(edgeWriter, edgeKeys, callsite, callerRva, null, sym, sym, "direct_external");
                return;
            }
            writeEdge(edgeWriter, edgeKeys, callsite, callerRva, rva(targetFn.getEntryPoint()), null, null, "direct_internal");
            return;
        }
        if (to.isExternalAddress()) {
            String sym = externalName(to);
            String resolution = sym != null && sym.contains("stub") ? "external_via_stub" : "direct_external";
            writeEdge(edgeWriter, edgeKeys, callsite, callerRva, null, sym, sym, resolution);
            return;
        }
        if (ref.getReferenceType() == RefType.COMPUTED_JUMP || ref.getReferenceType() == RefType.INDIRECTION) {
            writeEdge(edgeWriter, edgeKeys, callsite, callerRva, null, null, null, "unresolved_indirect");
            return;
        }
        writeEdge(edgeWriter, edgeKeys, callsite, callerRva, null, externalName(to), null, "unresolved_other");
    }

    private String externalName(Address addr) {
        Symbol[] syms = symbolTable.getSymbols(addr);
        if (syms != null && syms.length > 0) {
            return syms[0].getName();
        }
        ExternalLocation ext = currentProgram.getExternalManager().getExternalLocation(addr);
        if (ext != null && ext.getLabel() != null) {
            return ext.getLabel();
        }
        return null;
    }

    private void writeEdge(BufferedWriter w, Set<String> keys, String callsite, String caller,
            String calleeInternal, String calleeExternal, String ultimateExternal, String resolution) throws Exception {
        String key = callsite + "|" + resolution + "|" + calleeInternal + "|" + calleeExternal + "|" + ultimateExternal;
        if (keys.contains(key)) {
            return;
        }
        keys.add(key);
        StringBuilder line = new StringBuilder();
        line.append("{");
        line.append("\"callsite_rva\":").append(esc(callsite));
        line.append(",\"caller_rva\":").append(esc(caller));
        line.append(",\"callee_internal_rva\":").append(calleeInternal == null ? "null" : esc(calleeInternal));
        line.append(",\"callee_external_symbol\":").append(calleeExternal == null ? "null" : esc(calleeExternal));
        line.append(",\"ultimate_external_symbol\":").append(ultimateExternal == null ? "null" : esc(ultimateExternal));
        line.append(",\"resolution\":").append(esc(resolution));
        line.append("}\n");
        w.write(line.toString());
    }
}
