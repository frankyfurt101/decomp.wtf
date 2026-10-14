// Local analysis export. Drafts are never compiler-verified matches.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.AddressRange;
import com.google.gson.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.io.*;

public class SwitchExport extends GhidraScript {
    private String addr(ghidra.program.model.address.Address a) {
        return "0x" + a.toString();
    }
    @Override public void run() throws Exception {
        String[] args = getScriptArgs();
        Path destination = Paths.get(args[0]);
        int limit = Integer.parseInt(args[1]);
        Gson gson = new Gson();
        DecompInterface decompiler = new DecompInterface();
        if (!decompiler.openProgram(currentProgram)) {
            throw new IOException("Decompiler could not open the imported program");
        }
        try (PrintWriter writer = new PrintWriter(Files.newBufferedWriter(destination, StandardCharsets.UTF_8))) {
            JsonObject summary = new JsonObject();
            summary.addProperty("type", "summary");
            summary.addProperty("total_functions", currentProgram.getFunctionManager().getFunctionCount());
            summary.addProperty("image_base", addr(currentProgram.getImageBase()));
            summary.addProperty("language", currentProgram.getLanguageID().toString());
            writer.println(gson.toJson(summary)); writer.flush();
            int count = 0;
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext() && count < limit) {
                monitor.checkCancelled();
                Function f = functions.next();
                if (f.isExternal()) continue;
                JsonObject row = new JsonObject();
                row.addProperty("type", "function");
                row.addProperty("name", f.getName());
                row.addProperty("address", addr(f.getEntryPoint()));
                row.addProperty("relative_address", "0x" + Long.toUnsignedString(
                    f.getEntryPoint().getOffset() - currentProgram.getImageBase().getOffset(), 16));
                row.addProperty("size", f.getBody().getNumAddresses());
                JsonArray ranges = new JsonArray();
                for (AddressRange range : f.getBody()) {
                    JsonObject r = new JsonObject();
                    r.addProperty("start", addr(range.getMinAddress()));
                    r.addProperty("end", addr(range.getMaxAddress()));
                    ranges.add(r);
                }
                row.add("ranges", ranges);
                StringBuilder assembly = new StringBuilder();
                InstructionIterator instructions = currentProgram.getListing().getInstructions(f.getBody(), true);
                int lines = 0;
                while (instructions.hasNext() && lines++ < 4096) {
                    Instruction insn = instructions.next();
                    assembly.append(addr(insn.getAddress())).append("  ").append(insn.toString()).append('\n');
                }
                row.addProperty("assembly", assembly.toString());
                row.addProperty("assembly_truncated", instructions.hasNext());
                JsonArray calls = new JsonArray();
                for (Function called : f.getCalledFunctions(monitor)) {
                    JsonObject c = new JsonObject();
                    c.addProperty("name", called.getName()); c.addProperty("address", addr(called.getEntryPoint()));
                    calls.add(c);
                }
                row.add("calls", calls);
                try {
                    DecompileResults result = decompiler.decompileFunction(f, 20, monitor);
                    if (result != null && result.decompileCompleted() && result.getDecompiledFunction() != null) {
                        row.addProperty("status", "drafted");
                        row.addProperty("c", result.getDecompiledFunction().getC());
                    } else {
                        row.addProperty("status", "failed");
                        row.addProperty("error", result == null ? "no decompiler result" : result.getErrorMessage());
                    }
                } catch (Exception e) {
                    row.addProperty("status", "failed"); row.addProperty("error", e.toString());
                }
                writer.println(gson.toJson(row)); writer.flush(); count++;
            }
            JsonObject done = new JsonObject();
            done.addProperty("type", "complete"); done.addProperty("exported", count);
            writer.println(gson.toJson(done)); writer.flush();
        } finally { decompiler.dispose(); }
    }
}
