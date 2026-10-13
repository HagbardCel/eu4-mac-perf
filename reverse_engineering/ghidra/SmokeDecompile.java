// Decompile anchor symbols for D1 preflight.
//@category EU4.RE
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolTable;
import java.io.File;
import java.nio.file.Files;
import java.nio.charset.StandardCharsets;

public class SmokeDecompile extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            throw new IllegalArgumentException("usage: SmokeDecompile.java <report.json> [symbol ...]");
        }
        File report = new File(args[0]);
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        SymbolTable st = currentProgram.getSymbolTable();
        StringBuilder json = new StringBuilder();
        json.append("{\n  \"anchors\": [\n");
        boolean first = true;
        for (int i = 1; i < args.length; i++) {
            String needle = args[i];
            boolean ok = false;
            String matched = null;
            for (Symbol sym : st.getAllSymbols(true)) {
                String name = sym.getName();
                if (name == null) {
                    continue;
                }
                if (!name.contains(needle) && !sym.getName(true).contains(needle)) {
                    continue;
                }
                Function fn = getFunctionAt(sym.getAddress());
                if (fn == null) {
                    continue;
                }
                DecompileResults res = decomp.decompileFunction(fn, 120, monitor);
                ok = res != null && res.decompileCompleted() && res.getDecompiledFunction() != null;
                matched = name;
                break;
            }
            if (!first) {
                json.append(",\n");
            }
            first = false;
            json.append("    {\"needle\": \"").append(needle.replace("\"", "\\\""));
            json.append("\", \"matched\": ");
            json.append(matched == null ? "null" : "\"" + matched.replace("\"", "\\\"") + "\"");
            json.append(", \"success\": ").append(ok).append("}");
        }
        json.append("\n  ]\n}\n");
        Files.writeString(report.toPath(), json.toString(), StandardCharsets.UTF_8);
        decomp.dispose();
        println("SmokeDecompile wrote " + report.getAbsolutePath());
    }
}
