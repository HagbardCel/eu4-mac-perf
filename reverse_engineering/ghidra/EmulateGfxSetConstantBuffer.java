// Bounded offline execution pilot for one original EU IV function.
//
// Runs the frozen scenarios in <scenariosJson> through Ghidra's P-code emulator with an
// entirely constructed state (registers, context object, buffer length, stack) and emits
// an observation record. Nothing is launched: the program is read, its instructions are
// decoded and their P-code is interpreted.
//
// The emulator state is preloaded with the pinned binary's own instruction bytes, so the
// process can read nothing outside the declared regions. All contract comparison happens
// in Python (reverse_engineering/reconstruction_pilot.py); this script only observes, and
// marks a run unqualified when execution itself is not well formed.
//
//@category EU4.RE
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import ghidra.app.script.GhidraScript;
import ghidra.framework.Application;
import ghidra.pcode.emu.PcodeEmulationCallbacks;
import ghidra.pcode.emu.PcodeEmulator;
import ghidra.pcode.emu.PcodeThread;
import ghidra.pcode.emu.ThreadPcodeExecutorState;
import ghidra.pcode.exec.PcodeExecutorStatePiece;
import ghidra.pcode.exec.PcodeExecutorStatePiece.Reason;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.lang.Language;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.pcode.PcodeOp;

public class EmulateGfxSetConstantBuffer extends GhidraScript {

    private static final String SCRIPT_VERSION = "EmulateGfxSetConstantBuffer/v1";
    private static final String SCHEMA = "eu4-re0-pilot-observations/v1";

    /** Hard ceiling on stepped instructions; the longest frozen path is 18. */
    private static final int STEP_BUDGET = 64;

    /** The instruction bytes are preloaded for the body plus this margin. */
    private static final int CODE_MARGIN = 16;

    private static final String[] GENERAL_PURPOSE = {
        "RAX", "RBX", "RCX", "RDX", "RSI", "RDI", "RBP", "RSP",
        "R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15"
    };

    /** Observes execution. Everything recorded here is execution-time; setup is not. */
    private static final class Trace implements PcodeEmulationCallbacks<byte[]> {

        private boolean armed = false;
        private long imageBase = 0;
        private String dataSpaceName = "";

        private final List<String[]> executed = new ArrayList<>();
        private final List<String[]> loads = new ArrayList<>();
        private final List<String[]> stores = new ArrayList<>();
        private final List<String[]> dataWritten = new ArrayList<>();
        private final List<String[]> uninitializedReads = new ArrayList<>();
        private final List<String> missingUserops = new ArrayList<>();

        @Override
        public void beforeExecuteInstruction(PcodeThread<byte[]> thread, Instruction instruction,
                ghidra.pcode.exec.PcodeProgram program) {
            if (!armed) {
                return;
            }
            String bytes;
            try {
                bytes = hex(instruction.getBytes());
            }
            catch (Exception e) {
                bytes = "unreadable:" + e.getClass().getSimpleName();
            }
            executed.add(new String[] {
                "0x" + Long.toHexString(instruction.getAddress().getOffset() - imageBase),
                bytes, instruction.toString()
            });
        }

        @Override
        public void beforeLoad(PcodeThread<byte[]> thread, PcodeOp op, AddressSpace space,
                byte[] offset, int size) {
            if (!armed) {
                return;
            }
            loads.add(new String[] {
                space.getName(), hexOf(asLong(offset)), Integer.toString(size)
            });
        }

        @Override
        public void beforeStore(PcodeThread<byte[]> thread, PcodeOp op, AddressSpace space,
                byte[] offset, int size, byte[] value) {
            if (!armed) {
                return;
            }
            stores.add(new String[] {
                space.getName(), hexOf(asLong(offset)), Integer.toString(size), hex(value)
            });
        }

        // The state-space write path invokes the concrete-address overload; the abstract-offset
        // overload is only forwarded to from it, so recording here observes each executed write
        // exactly once. Register and temporary-space writes are Ghidra's own bookkeeping, not
        // original-function memory effects, so only the data space is recorded.
        @Override
        public <A, U> void dataWritten(PcodeThread<byte[]> thread,
                PcodeExecutorStatePiece<A, U> piece, Address address, int size, U value) {
            if (!armed || !address.getAddressSpace().getName().equals(dataSpaceName)) {
                return;
            }
            String text = value instanceof byte[] ? hex((byte[]) value) : String.valueOf(value);
            dataWritten.add(new String[] {
                address.getAddressSpace().getName(), hexOf(address.getOffset()),
                Integer.toString(size), text
            });
        }

        @Override
        public <A, U> int readUninitialized(PcodeThread<byte[]> thread,
                PcodeExecutorStatePiece<A, U> piece, AddressSpace space, A offset, int length,
                Reason reason) {
            if (space.isUniqueSpace() || space.isRegisterSpace() || space.isConstantSpace()) {
                // Ghidra-internal temporary state, not an original-function memory effect.
                return 0;
            }
            uninitializedReads.add(new String[] {
                space.getName(), hexOf(asLong(offset)), Integer.toString(length),
                reason.name()
            });
            return 0;
        }

        @Override
        public boolean handleMissingUserop(PcodeThread<byte[]> thread, PcodeOp op,
                ghidra.pcode.exec.PcodeFrame frame, String userop,
                ghidra.pcode.exec.PcodeUseropLibrary<byte[]> library) {
            missingUserops.add(userop);
            return false;
        }
    }

    // ---------------------------------------------------------------- numeric helpers

    private static byte[] le(long value, int size) {
        byte[] out = new byte[size];
        for (int i = 0; i < size; i++) {
            out[i] = (byte) ((value >>> (8 * i)) & 0xff);
        }
        return out;
    }

    private static long toLong(byte[] value) {
        long out = 0;
        for (int i = value.length - 1; i >= 0; i--) {
            out = (out << 8) | (value[i] & 0xffL);
        }
        return out;
    }

    private static String hex(byte[] value) {
        StringBuilder sb = new StringBuilder();
        for (byte b : value) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    private static String hexOf(Long value) {
        return value == null ? "unknown" : "0x" + Long.toHexString(value);
    }

    private static Long asLong(Object value) {
        if (value instanceof byte[]) {
            return toLong((byte[]) value);
        }
        if (value instanceof Number) {
            return ((Number) value).longValue();
        }
        return null;
    }

    private static long parseHex(String text) {
        String t = text.trim();
        if (t.startsWith("0x") || t.startsWith("0X")) {
            t = t.substring(2);
        }
        return Long.parseUnsignedLong(t, 16);
    }

    private static long parseHexAt(JsonObject object, String field) {
        return parseHex(object.get(field).getAsString());
    }

    private static String sha256(byte[] data) throws Exception {
        byte[] hash = MessageDigest.getInstance("SHA-256").digest(data);
        StringBuilder sb = new StringBuilder();
        for (byte b : hash) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    private static JsonArray rows(List<String[]> source) {
        JsonArray array = new JsonArray();
        for (String[] row : source) {
            JsonArray item = new JsonArray();
            for (String cell : row) {
                item.add(cell);
            }
            array.add(item);
        }
        return array;
    }

    private static JsonObject strings(Map<String, String> map) {
        JsonObject object = new JsonObject();
        for (Map.Entry<String, String> entry : map.entrySet()) {
            object.addProperty(entry.getKey(), entry.getValue());
        }
        return object;
    }

    private static byte[] fillPattern(int size) {
        byte[] out = new byte[size];
        for (int i = 0; i < size; i++) {
            out[i] = (byte) (((i * 0x37) + 0x11) & 0xff);
        }
        return out;
    }

    private static void put(byte[] region, long base, long address, byte[] value) {
        System.arraycopy(value, 0, region, (int) (address - base), value.length);
    }

    private static byte[] slice(byte[] region, long base, long address, int length) {
        byte[] out = new byte[length];
        System.arraycopy(region, (int) (address - base), out, 0, length);
        return out;
    }

    // ---------------------------------------------------------------- the pilot

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            throw new IllegalArgumentException(
                "usage: EmulateGfxSetConstantBuffer.java <scenariosJson> <outputJson>");
        }
        File scenariosFile = new File(args[0]);
        File outputFile = new File(args[1]);

        byte[] scenariosBytes = Files.readAllBytes(scenariosFile.toPath());
        String scenariosSha = sha256(scenariosBytes);
        JsonObject scenarios =
            JsonParser.parseString(new String(scenariosBytes, StandardCharsets.UTF_8))
                    .getAsJsonObject();

        long imageBase = scenarios.get("image_base").getAsLong();
        long entryVa = imageBase + parseHex(scenarios.get("entry_rva").getAsString());
        long endVa = imageBase + parseHex(scenarios.get("end_rva").getAsString());

        Language language = currentProgram.getLanguage();
        AddressSpace space = language.getDefaultSpace();
        Address entry = currentProgram.getAddressFactory().getDefaultAddressSpace()
                .getAddress(entryVa);

        // Code window: the pinned binary's own bytes, the emulator's only source of
        // instructions. There is no program-memory backing, so nothing else can be fetched.
        int codeLength = (int) (endVa - entryVa) + CODE_MARGIN;
        byte[] code = new byte[codeLength];
        int got = currentProgram.getMemory().getBytes(entry, code);
        if (got < codeLength) {
            byte[] exact = new byte[(int) (endVa - entryVa)];
            currentProgram.getMemory().getBytes(entry, exact);
            code = new byte[codeLength];
            System.arraycopy(exact, 0, code, 0, exact.length);
        }

        JsonArray results = new JsonArray();
        for (JsonElement element : scenarios.getAsJsonArray("scenarios")) {
            results.add(runScenario(element.getAsJsonObject(), scenarios, language, space, entry,
                entryVa, endVa, imageBase, code));
        }

        JsonObject environment = new JsonObject();
        environment.addProperty("script_version", SCRIPT_VERSION);
        environment.addProperty("ghidra_version", Application.getApplicationVersion());
        environment.addProperty("java_version", System.getProperty("java.version"));
        environment.addProperty("program_name", currentProgram.getName());
        environment.addProperty("program_path", currentProgram.getExecutablePath());
        environment.addProperty("image_base", hexOf(imageBase));

        JsonObject canonical = new JsonObject();
        canonical.addProperty("function", scenarios.get("function").getAsString());
        canonical.addProperty("entry_rva", scenarios.get("entry_rva").getAsString());
        canonical.addProperty("end_rva", scenarios.get("end_rva").getAsString());
        canonical.addProperty("binary_sha256", scenarios.get("binary_sha256").getAsString());
        canonical.addProperty("executable_sha256",
            String.valueOf(currentProgram.getExecutableSHA256()));
        canonical.addProperty("scenarios_sha256", scenariosSha);
        canonical.addProperty("code_window_sha256", sha256(code));
        canonical.add("results", results);

        JsonObject root = new JsonObject();
        root.addProperty("schema", SCHEMA);
        root.add("environment", environment);
        root.add("canonical", canonical);

        Gson gson = new GsonBuilder().setPrettyPrinting().create();
        Files.writeString(outputFile.toPath(), gson.toJson(root) + "\n");
        println("wrote " + outputFile.getAbsolutePath());
    }

    private JsonObject runScenario(JsonObject scenario, JsonObject scenarios, Language language,
            AddressSpace space, Address entry, long entryVa, long endVa, long imageBase,
            byte[] code) {

        JsonObject memory = scenarios.getAsJsonObject("memory");
        long ctxVa = parseHexAt(memory, "ctx");
        long handleVa = parseHexAt(memory, "handle");
        long bufferVa = parseHexAt(memory, "buffer");
        long stackBase = parseHexAt(memory, "stack_base");
        long rsp0 = parseHexAt(memory, "rsp0");
        int ctxSize = (int) parseHex(memory.get("ctx_size").getAsString());
        int stackSize = (int) parseHex(memory.get("stack_size").getAsString());

        JsonObject constants = scenarios.getAsJsonObject("constants");
        long rbpInitial = parseHexAt(constants, "rbp_initial");
        long sentinel = parseHexAt(constants, "return_sentinel");

        JsonObject layout = scenarios.getAsJsonObject("layout");
        long canaryBefore = parseHexAt(layout, "canary_before_start");
        long canaryAfter = parseHexAt(layout, "canary_after_end");
        int stackWindowPre = (int) parseHex(layout.get("stack_window_before").getAsString());
        int stackWindowPost = (int) parseHex(layout.get("stack_window_after").getAsString());

        String id = scenario.get("id").getAsString();
        long slot = scenario.get("slot").getAsLong();
        long r9d = parseHex(scenario.get("r9d").getAsString());
        boolean hasBufferValue = scenario.has("buffer_first_dword") &&
            !scenario.get("buffer_first_dword").isJsonNull();

        // Constructed memory. The buffer region exists only where a scenario declares a
        // length for it; elsewhere a read of [RSI] must not happen at all.
        byte[] ctx = fillPattern(ctxSize);
        byte[] handle = le(ctxVa, 8);
        byte[] stack = fillPattern(stackSize);
        put(stack, stackBase, rsp0, le(sentinel, 8));
        byte[] buffer = hasBufferValue
            ? le(parseHex(scenario.get("buffer_first_dword").getAsString()), 4) : null;

        // Recorded windows: the observation region plus both canary bands, and the stack frame
        // area. Anything else a store touched still shows up in the exhaustive store traces.
        long ctxWindowVa = ctxVa + canaryBefore;
        int ctxWindowLength = (int) (canaryAfter - canaryBefore);
        long stackWindowVa = rsp0 - stackWindowPre;
        int stackWindowLength = stackWindowPre + stackWindowPost;
        byte[] ctxWindowBefore = slice(ctx, ctxVa, ctxWindowVa, ctxWindowLength);
        byte[] stackWindowBefore = slice(stack, stackBase, stackWindowVa, stackWindowLength);

        Trace trace = new Trace();
        PcodeEmulator emulator = new PcodeEmulator(language, trace);
        PcodeThread<byte[]> thread = emulator.newThread();
        thread.overrideContextWithDefault();
        ThreadPcodeExecutorState<byte[]> state = thread.getState();

        Map<String, Long> registers = new LinkedHashMap<>();
        for (String name : GENERAL_PURPOSE) {
            registers.put(name, 0L);
        }
        registers.put("RCX", slot);
        registers.put("R9", r9d);
        registers.put("RSI", bufferVa);
        registers.put("RDI", handleVa);
        registers.put("RBP", rbpInitial);
        registers.put("RSP", rsp0);
        for (Map.Entry<String, Long> entryRegister : registers.entrySet()) {
            Register register = language.getRegister(entryRegister.getKey());
            if (register == null) {
                throw new IllegalStateException("unknown register " + entryRegister.getKey());
            }
            state.setVar(register, le(entryRegister.getValue(), register.getNumBytes()));
        }

        state.setVar(space, ctxVa, ctx.length, false, ctx);
        state.setVar(space, handleVa, handle.length, false, handle);
        state.setVar(space, entry.getOffset(), code.length, false, code);
        if (buffer != null) {
            state.setVar(space, bufferVa, buffer.length, false, buffer);
        }
        state.setVar(space, stackBase, stack.length, false, stack);

        thread.setCounter(entry);

        List<String> reasons = new ArrayList<>();
        int steps = 0;
        boolean budgetExceeded = false;

        trace.imageBase = imageBase;
        trace.dataSpaceName = space.getName();
        trace.armed = true;
        try {
            for (steps = 0; steps < STEP_BUDGET; steps++) {
                long pc = thread.getCounter().getOffset();
                if (pc < entryVa || pc >= endVa) {
                    break;
                }
                thread.stepInstruction();
            }
            budgetExceeded = steps >= STEP_BUDGET;
        }
        catch (Throwable e) {
            reasons.add("execution failed: " + e.getClass().getSimpleName() + ": " + e.getMessage());
        }
        trace.armed = false;

        if (budgetExceeded) {
            reasons.add("step budget of " + STEP_BUDGET + " exhausted");
        }
        long pcAfter = thread.getCounter().getOffset();
        if (pcAfter != sentinel) {
            reasons.add("counter did not reach the return sentinel (0x" +
                Long.toHexString(pcAfter) + ")");
        }
        if (trace.executed.isEmpty()) {
            reasons.add("no instruction executed");
        }
        else if (!hexOf(endVa - imageBase - 1).equals(trace.executed.get(trace.executed.size() - 1)[0])) {
            reasons.add("execution did not end by executing the original RET");
        }
        if (!trace.missingUserops.isEmpty()) {
            reasons.add("missing userop(s): " + String.join(",", trace.missingUserops));
        }
        if (!trace.uninitializedReads.isEmpty()) {
            reasons.add("uninitialized read from the constructed state");
        }

        Map<String, String> finalRegisters = new LinkedHashMap<>();
        for (String name : GENERAL_PURPOSE) {
            finalRegisters.put(name,
                hexOf(toLong(state.getVar(language.getRegister(name), Reason.INSPECT))));
        }
        byte[] ctxWindowAfter =
            state.getVar(space, ctxWindowVa, ctxWindowLength, false, Reason.INSPECT);
        byte[] stackWindowAfter =
            state.getVar(space, stackWindowVa, stackWindowLength, false, Reason.INSPECT);

        JsonObject result = new JsonObject();
        result.addProperty("id", id);
        result.addProperty("path", scenario.get("path").getAsString());
        result.addProperty("slot", slot);
        result.addProperty("r9d", hexOf(r9d));
        result.add("executed", rows(trace.executed));
        result.add("loads", rows(trace.loads));
        result.add("stores", rows(trace.stores));
        result.add("data_written", rows(trace.dataWritten));
        result.add("uninitialized_reads", rows(trace.uninitializedReads));
        result.add("final_registers", strings(finalRegisters));
        result.addProperty("ctx_window_base", hexOf(ctxWindowVa));
        result.addProperty("stack_window_base", hexOf(stackWindowVa));
        result.addProperty("ctx_window_before", hex(ctxWindowBefore));
        result.addProperty("ctx_window_after", hex(ctxWindowAfter));
        result.addProperty("stack_window_before", hex(stackWindowBefore));
        result.addProperty("stack_window_after", hex(stackWindowAfter));
        result.addProperty("qualified", reasons.isEmpty());
        JsonArray reasonArray = new JsonArray();
        for (String reason : reasons) {
            reasonArray.add(reason);
        }
        result.add("reasons", reasonArray);
        return result;
    }
}
