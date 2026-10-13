// Writes program metadata after first import.
//@category EU4.RE
import ghidra.app.script.GhidraScript;
import java.io.File;
import java.nio.file.Files;
import java.nio.charset.StandardCharsets;

public class RecordProjectMetadata extends GhidraScript {

    private static String jsonEscape(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            throw new IllegalArgumentException("usage: RecordProjectMetadata.java <output.json>");
        }
        File out = new File(args[0]);
        String programName = currentProgram.getName();
        String domainPath = currentProgram.getDomainFile().getPathname();
        long imageBase = currentProgram.getImageBase().getOffset();
        String languageId = currentProgram.getLanguageID().getIdAsString();
        String compilerId = currentProgram.getCompilerSpec().getCompilerSpecID().getIdAsString();
        String json = "{\n"
            + "  \"program_name\": " + jsonEscape(programName) + ",\n"
            + "  \"program_path_in_project\": " + jsonEscape(domainPath) + ",\n"
            + "  \"image_base\": " + imageBase + ",\n"
            + "  \"language_id\": " + jsonEscape(languageId) + ",\n"
            + "  \"compiler_spec_id\": " + jsonEscape(compilerId) + "\n"
            + "}\n";
        Files.writeString(out.toPath(), json, StandardCharsets.UTF_8);
        println("RecordProjectMetadata wrote " + out.getAbsolutePath());
    }
}
