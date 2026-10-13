// Decompile a fixed batch of functions (RVA list file).
//@category EU4.RE
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.nio.file.Files;
import java.util.List;

public class ExportDecompilation extends GhidraScript {

    private static String esc(String s) {
        if (s == null) {
            return "null";
        }
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r") + "\"";
    }

    private long parseRva(String text) {
        String t = text.trim();
        if (t.startsWith("0x") || t.startsWith("0X")) {
            t = t.substring(2);
        }
        return Long.parseUnsignedLong(t, 16);
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 3) {
            throw new IllegalArgumentException(
                "usage: ExportDecompilation.java <rvaListFile> <outputJsonl> <timeoutSeconds>");
        }
        File rvaFile = new File(args[0]);
        File outFile = new File(args[1]);
        int timeoutSec = Integer.parseInt(args[2]);
        long imageBase = currentProgram.getImageBase().getOffset();
        FunctionManager fm = currentProgram.getFunctionManager();

        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        decomp.setSimplificationStyle("decompile");

        BufferedWriter writer = new BufferedWriter(new FileWriter(outFile));
        List<String> rvas = Files.readAllLines(rvaFile.toPath());
        for (String line : rvas) {
            if (line.trim().isEmpty() || line.startsWith("#")) {
                continue;
            }
            long rva = parseRva(line);
            Address entry = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(imageBase + rva);
            Function fn = fm.getFunctionAt(entry);
            if (fn == null) {
                fn = fm.getFunctionContaining(entry);
            }
            long start = System.nanoTime();
            String status = "failed";
            String pseudocode = null;
            String error = null;
            if (fn == null) {
                status = "skipped";
                error = "no function at entry";
            } else {
                DecompileResults results = decomp.decompileFunction(fn, timeoutSec, monitor);
                double seconds = (System.nanoTime() - start) / 1_000_000_000.0;
                if (results != null && results.decompileCompleted() && results.getDecompiledFunction() != null) {
                    status = "success";
                    pseudocode = results.getDecompiledFunction().getC();
                } else if (results != null && results.isTimedOut()) {
                    status = "timeout";
                    error = "decompiler timeout";
                } else {
                    status = "failed";
                    error = results != null ? results.getErrorMessage() : "unknown decompile failure";
                }
                writer.write(record(fn, imageBase, status, seconds, pseudocode, error));
                writer.flush();
                continue;
            }
            double seconds = (System.nanoTime() - start) / 1_000_000_000.0;
            writer.write(record(null, imageBase, status, seconds, pseudocode, error, rva));
            writer.flush();
        }
        writer.close();
        decomp.dispose();
        println("ExportDecompilation wrote " + outFile.getAbsolutePath());
    }

    private String record(Function fn, long imageBase, String status, double seconds, String pseudo, String error)
            throws Exception {
        long rva = fn.getEntryPoint().getOffset() - imageBase;
        return record(fn, imageBase, status, seconds, pseudo, error, rva);
    }

    private String record(Function fn, long imageBase, String status, double seconds, String pseudo, String error, long rva) {
        StringBuilder sb = new StringBuilder();
        sb.append("{");
        sb.append("\"entry_rva\":").append(esc(String.format("0x%016x", rva)));
        if (fn != null) {
            sb.append(",\"name_mangled\":").append(esc(fn.getName()));
        }
        sb.append(",\"status\":").append(esc(status));
        sb.append(",\"decompilation_seconds\":").append(seconds);
        if (pseudo != null) {
            sb.append(",\"pseudocode\":").append(esc(pseudo));
        }
        if (error != null) {
            sb.append(",\"error\":").append(esc(error));
        }
        sb.append("}\n");
        return sb.toString();
    }
}
