// One-shot read-only query. Headless must pass -noanalysis -readOnly.
// Image bytes are the imported program, not a value filled in at runtime.
//@category EU4.RE
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressOutOfBoundsException;
import ghidra.program.model.address.AddressRange;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.listing.Parameter;
import ghidra.program.model.mem.MemoryAccessException;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.Varnode;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.RefType;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolIterator;
import ghidra.program.model.symbol.SymbolType;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class QueryProgram extends GhidraScript {

    private long imageBase;
    private AddressSpace space;

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            throw new IllegalArgumentException("usage: QueryProgram.java <request.json> <response.json>");
        }
        JsonObject request = JsonParser.parseString(Files.readString(Path.of(args[0]))).getAsJsonObject();
        imageBase = currentProgram.getImageBase().getOffset();
        space = currentProgram.getAddressFactory().getDefaultAddressSpace();
        JsonObject out = identity();
        String op = request.get("op").getAsString();
        if ("function".equals(op)) {
            writeFunction(request, out);
        } else if ("address".equals(op)) {
            writeAddress(request, out);
        } else {
            throw new IllegalArgumentException("unsupported op");
        }
        Files.writeString(Path.of(args[1]), out.toString(), StandardCharsets.UTF_8);
    }

    private JsonObject identity() {
        JsonObject out = new JsonObject();
        out.addProperty("program_name", currentProgram.getName());
        out.addProperty("image_base", imageBase);
        out.addProperty("executable_sha256", currentProgram.getExecutableSHA256());
        return out;
    }

    private void writeFunction(JsonObject request, JsonObject out) throws Exception {
        List<Function> matches = resolve(request);
        out.addProperty("match_total", matches.size());
        if (matches.isEmpty()) {
            out.addProperty("presence", "absent");
            return;
        }
        if (matches.size() > 1) {
            out.addProperty("presence", "ambiguous");
            JsonArray rows = new JsonArray();
            for (Function fn : matches) {
                JsonObject row = new JsonObject();
                row.addProperty("entry_rva", rva(fn.getEntryPoint()));
                row.addProperty("name", fn.getName());
                rows.add(row);
            }
            out.add("matches", rows);
            return;
        }
        Function fn = matches.get(0);
        out.addProperty("presence", "present");
        out.addProperty("entry_rva", rva(fn.getEntryPoint()));
        out.addProperty("name", fn.getName());
        out.addProperty("thunk", fn.isThunk());
        out.addProperty("no_body", fn.getBody().isEmpty());
        JsonArray body = new JsonArray();
        for (AddressRange range : fn.getBody()) {
            JsonObject row = new JsonObject();
            row.addProperty("start_rva", rva(range.getMinAddress()));
            row.addProperty("end_rva", rva(range.getMaxAddress().add(1)));
            body.add(row);
        }
        out.add("body", body);
        hypothesis(fn, out);
        decompile(fn, request, out);
        List<JsonObject> instructions = instructions(fn);
        out.addProperty("instruction_total", instructions.size());
        out.add("instructions", array(slice(instructions, num(request, "instruction_offset", 0), num(request, "instruction_limit", 128))));
        List<Function> callers = sorted(fn.getCallingFunctions(monitor));
        List<Function> callees = sorted(fn.getCalledFunctions(monitor));
        out.addProperty("caller_total", callers.size());
        out.add("callers", functions(slice(callers, num(request, "caller_offset", 0), num(request, "caller_limit", 64))));
        out.addProperty("callee_total", callees.size());
        out.add("callees", functions(slice(callees, num(request, "callee_offset", 0), num(request, "callee_limit", 64))));
    }

    private List<Function> resolve(JsonObject request) {
        if (request.has("name") && !request.get("name").isJsonNull()) {
            String query = request.get("name").getAsString();
            String base = query;
            int split = query.lastIndexOf("::");
            if (split >= 0 && split + 2 < query.length()) {
                base = query.substring(split + 2);
            }
            Map<Address, Function> found = new LinkedHashMap<>();
            SymbolIterator symbols = currentProgram.getSymbolTable().getSymbols(base);
            while (symbols.hasNext()) {
                Symbol symbol = symbols.next();
                if (symbol.getSymbolType() != SymbolType.FUNCTION) {
                    continue;
                }
                // Exact symbol name, or the same name with its namespace (`CGraphics::BeginScene`).
                if (!query.equals(symbol.getName()) && !query.equals(symbol.getName(true))) {
                    continue;
                }
                if (symbol.getObject() instanceof Function) {
                    Function fn = (Function) symbol.getObject();
                    found.putIfAbsent(fn.getEntryPoint(), fn);
                }
            }
            List<Function> matches = new ArrayList<>(found.values());
            matches.sort(Comparator.comparingLong(fn -> fn.getEntryPoint().getOffset()));
            return matches;
        }
        Address entry = address(request.get("address").getAsLong());
        Function fn = entry == null ? null : currentProgram.getFunctionManager().getFunctionAt(entry);
        List<Function> matches = new ArrayList<>();
        if (fn != null) {
            matches.add(fn);
        }
        return matches;
    }

    private void hypothesis(Function fn, JsonObject out) {
        JsonObject hypothesis = new JsonObject();
        hypothesis.addProperty("calling_convention", fn.getCallingConventionName());
        hypothesis.addProperty("return_type", fn.getReturnType() == null ? null : fn.getReturnType().getDisplayName());
        JsonArray params = new JsonArray();
        for (Parameter parameter : fn.getParameters()) {
            JsonObject row = new JsonObject();
            row.addProperty("name", parameter.getName());
            row.addProperty("type", parameter.getDataType() == null ? null : parameter.getDataType().getDisplayName());
            params.add(row);
        }
        hypothesis.add("parameters", params);
        out.add("hypothesis", hypothesis);
    }

    private void decompile(Function fn, JsonObject request, JsonObject out) {
        String status = "failed";
        String error = "decompilation failed";
        String text = "";
        DecompInterface decomp = new DecompInterface();
        try {
            decomp.openProgram(currentProgram);
            DecompileResults results = decomp.decompileFunction(fn, num(request, "decompile_timeout", 60), monitor);
            if (results != null && results.decompileCompleted() && results.getDecompiledFunction() != null) {
                status = "success";
                error = null;
                text = results.getDecompiledFunction().getC();
                if (text == null) {
                    text = "";
                }
            } else if (results != null && results.isTimedOut()) {
                status = "timeout";
                error = "decompiler timeout";
            } else if (results != null && results.getErrorMessage() != null && !results.getErrorMessage().isBlank()) {
                error = results.getErrorMessage();
            }
        } catch (Exception exc) {
            error = exc.getMessage() == null ? "decompilation failed" : exc.getMessage();
        } finally {
            decomp.dispose();
        }
        int total = text.length();
        int start = Math.min(Math.max(num(request, "decompile_offset", 0), 0), total);
        int end = Math.min(total, start + Math.max(num(request, "decompile_limit", 0), 0));
        out.addProperty("decompile_status", status);
        if (error != null) {
            out.addProperty("decompile_error", error);
        }
        out.addProperty("pseudocode_total", total);
        out.addProperty("pseudocode", text.substring(start, end));
    }

    private List<JsonObject> instructions(Function fn) throws MemoryAccessException {
        List<JsonObject> rows = new ArrayList<>();
        Listing listing = currentProgram.getListing();
        for (Instruction ins : listing.getInstructions(fn.getBody(), true)) {
            JsonObject row = new JsonObject();
            row.addProperty("rva", rva(ins.getAddress()));
            row.addProperty("bytes", hex(ins.getBytes()));
            row.addProperty("mnemonic", ins.getMnemonicString());
            row.addProperty("text", ins.toString());
            row.add("references_from", referencesFrom(ins));
            rows.add(row);
        }
        return rows;
    }

    private JsonArray referencesFrom(Instruction ins) {
        JsonArray rows = new JsonArray();
        Reference[] refs = ins.getReferencesFrom();
        if (refs == null) {
            return rows;
        }
        for (Reference ref : refs) {
            if (ref == null) {
                continue;
            }
            JsonObject row = new JsonObject();
            row.addProperty("ref_type", ref.getReferenceType().getName());
            Address to = ref.getToAddress();
            String target = rva(to);
            if (target != null) {
                row.addProperty("target_rva", target);
            } else if (to != null) {
                row.addProperty("target_external", to.toString());
            }
            rows.add(row);
        }
        return rows;
    }

    private void writeAddress(JsonObject request, JsonObject out) {
        Address addr = address(request.get("address").getAsLong());
        if (addr == null || currentProgram.getMemory().getBlock(addr) == null) {
            out.addProperty("presence", "unmapped");
            return;
        }
        MemoryBlock block = currentProgram.getMemory().getBlock(addr);
        Listing listing = currentProgram.getListing();
        Instruction ins = listing.getInstructionAt(addr);
        Instruction containing = listing.getInstructionContaining(addr);
        Data defined = definedData(listing, addr);
        out.addProperty("presence", "present");
        out.addProperty("initialized", block.isInitialized());
        boolean isDefined = ins != null || containing != null || defined != null;
        out.addProperty("defined", isDefined);
        if (ins != null) {
            out.addProperty("defined_kind", "instruction");
        } else if (containing != null) {
            out.addProperty("defined_kind", "instruction");
            out.addProperty("instruction_start_rva", rva(containing.getAddress()));
        } else if (defined != null) {
            out.addProperty("defined_kind", "data");
            if (!defined.getAddress().equals(addr)) {
                out.addProperty("defined_at_rva", rva(defined.getAddress()));
            }
        } else {
            out.addProperty("defined_kind", "undefined");
        }
        if (defined != null && defined.getDataType() != null) {
            out.addProperty("data_type", defined.getDataType().getDisplayName());
        }
        Symbol symbol = currentProgram.getSymbolTable().getPrimarySymbol(addr);
        if (symbol != null) {
            out.addProperty("symbol", symbol.getName());
        }
        if (block.isInitialized()) {
            out.addProperty("value_kind", "static_image");
            out.addProperty("static_bytes", readBytes(addr, block));
        } else {
            out.addProperty("value_kind", "unavailable");
        }
        if (ins != null) {
            JsonObject machine = new JsonObject();
            machine.addProperty("rva", rva(addr));
            try {
                machine.addProperty("bytes", hex(ins.getBytes()));
            } catch (MemoryAccessException exc) {
                machine.addProperty("bytes", "");
            }
            machine.addProperty("text", ins.toString());
            out.add("machine", machine);
            List<JsonObject> ops = pcode(ins);
            out.addProperty("pcode_total", ops.size());
            out.add("pcode", array(slice(ops, num(request, "pcode_offset", 0), num(request, "pcode_limit", 96))));
        } else {
            out.addProperty("pcode_total", 0);
            out.add("pcode", new JsonArray());
        }
        List<JsonObject> code = new ArrayList<>();
        List<JsonObject> data = new ArrayList<>();
        ReferenceIterator refs = currentProgram.getReferenceManager().getReferencesTo(addr);
        while (refs.hasNext()) {
            Reference ref = refs.next();
            JsonObject row = reference(ref, addr);
            if (row.get("kind").getAsString().equals("data")) {
                data.add(row);
            } else {
                code.add(row);
            }
        }
        Comparator<JsonObject> byFrom = Comparator.comparing(row -> row.get("from_rva").getAsString());
        code.sort(byFrom);
        data.sort(byFrom);
        out.addProperty("code_ref_total", code.size());
        out.add("code_refs", array(slice(code, num(request, "code_ref_offset", 0), num(request, "code_ref_limit", 64))));
        out.addProperty("data_ref_total", data.size());
        out.add("data_refs", array(slice(data, num(request, "data_ref_offset", 0), num(request, "data_ref_limit", 64))));
    }

    private Data definedData(Listing listing, Address addr) {
        Data at = listing.getDefinedDataAt(addr);
        if (at != null) {
            return at;
        }
        Data containing = listing.getDataContaining(addr);
        if (containing == null) {
            return null;
        }
        return listing.getDefinedDataAt(containing.getAddress());
    }

    private JsonObject reference(Reference ref, Address target) {
        RefType type = ref.getReferenceType();
        JsonObject row = new JsonObject();
        row.addProperty("kind", type.isData() ? "data" : "code");
        row.addProperty("ref_type", type.getName());
        String from = rva(ref.getFromAddress());
        row.addProperty("from_rva", from == null ? ref.getFromAddress().toString() : from);
        Function caller = currentProgram.getFunctionManager().getFunctionContaining(ref.getFromAddress());
        if (caller != null && !caller.isExternal()) {
            row.addProperty("from_function_rva", rva(caller.getEntryPoint()));
            row.addProperty("from_function", caller.getName());
        }
        boolean unresolved = false;
        if (type.isCall() || type.isComputed() || type.isIndirect()) {
            unresolved = currentProgram.getFunctionManager().getFunctionContaining(target) == null;
        } else if (type.isData()) {
            unresolved = definedData(currentProgram.getListing(), target) == null;
        }
        row.addProperty("unresolved", unresolved);
        return row;
    }

    private List<JsonObject> pcode(Instruction ins) {
        List<JsonObject> rows = new ArrayList<>();
        PcodeOp[] ops = ins.getPcode();
        if (ops == null) {
            return rows;
        }
        for (PcodeOp op : ops) {
            JsonObject row = new JsonObject();
            row.addProperty("mnemonic", op.getMnemonic());
            if (op.getOutput() != null) {
                row.add("output", varnode(op.getOutput()));
            }
            JsonArray inputs = new JsonArray();
            for (Varnode input : op.getInputs()) {
                inputs.add(varnode(input));
            }
            row.add("inputs", inputs);
            rows.add(row);
        }
        return rows;
    }

    private JsonObject varnode(Varnode node) {
        JsonObject row = new JsonObject();
        Address addr = node.getAddress();
        row.addProperty("space", addr.getAddressSpace().getName());
        row.addProperty("offset", addr.getOffset());
        row.addProperty("size", node.getSize());
        if (node.isAddress()) {
            String asRva = rva(addr);
            if (asRva != null) {
                row.addProperty("rva", asRva);
            }
        }
        return row;
    }

    private String readBytes(Address addr, MemoryBlock block) {
        long available = block.getEnd().subtract(addr) + 1;
        int count = (int) Math.min(16, Math.max(available, 0));
        if (count == 0) {
            return "";
        }
        byte[] buf = new byte[count];
        try {
            currentProgram.getMemory().getBytes(addr, buf);
        } catch (MemoryAccessException exc) {
            return "";
        }
        return hex(buf);
    }

    private JsonArray functions(List<Function> fns) {
        JsonArray rows = new JsonArray();
        for (Function fn : fns) {
            JsonObject row = new JsonObject();
            row.addProperty("name", fn.getName());
            row.addProperty("external", fn.isExternal());
            if (!fn.isExternal()) {
                row.addProperty("entry_rva", rva(fn.getEntryPoint()));
            }
            rows.add(row);
        }
        return rows;
    }

    private List<Function> sorted(java.util.Set<Function> fns) {
        List<Function> rows = new ArrayList<>(fns);
        rows.sort(Comparator.comparingLong(fn -> fn.getEntryPoint().getOffset()));
        return rows;
    }

    private Address address(long value) {
        try {
            return space.getAddress(value);
        } catch (AddressOutOfBoundsException exc) {
            return null;
        }
    }

    private String rva(Address addr) {
        if (addr == null || !addr.isMemoryAddress() || !addr.getAddressSpace().equals(space)) {
            return null;
        }
        return String.format("0x%016x", addr.getOffset() - imageBase);
    }

    private static String hex(byte[] raw) {
        if (raw == null) {
            return "";
        }
        StringBuilder sb = new StringBuilder(raw.length * 2);
        for (byte b : raw) {
            sb.append(String.format("%02x", b & 0xff));
        }
        return sb.toString();
    }

    private static int num(JsonObject obj, String key, int fallback) {
        if (!obj.has(key) || obj.get(key).isJsonNull()) {
            return fallback;
        }
        return obj.get(key).getAsInt();
    }

    private static <T> List<T> slice(List<T> rows, int offset, int limit) {
        if (offset < 0) {
            offset = 0;
        }
        if (limit < 0 || offset >= rows.size()) {
            return List.of();
        }
        return rows.subList(offset, Math.min(rows.size(), offset + limit));
    }

    private static JsonArray array(List<JsonObject> rows) {
        JsonArray out = new JsonArray();
        for (JsonObject row : rows) {
            out.add(row);
        }
        return out;
    }
}
