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
import java.util.HashSet;
import java.util.Set;

public class SwitchExport extends GhidraScript {
    private String addr(ghidra.program.model.address.Address a) {
        return "0x" + a.toString();
    }
    @Override public void run() throws Exception {
        String[] args = getScriptArgs();
        Path destination = Paths.get(args[0]);
        int limit = Integer.parseInt(args[1]);
        int offset = args.length > 2 ? Integer.parseInt(args[2]) : 0;
        Gson gson = new Gson();
        // Resume by address: bounded rediscovery can change the function ordering.
        Set<String> completed = new HashSet<>();
        Path base = destination.resolveSibling("base-functions.jsonl");
        boolean hasBase = Files.exists(base);
        if (hasBase) {
            try (BufferedReader reader = Files.newBufferedReader(base, StandardCharsets.UTF_8)) {
                for (String line; (line = reader.readLine()) != null;) {
                    JsonObject row = JsonParser.parseString(line).getAsJsonObject();
                    if (row.has("type") && row.get("type").getAsString().equals("function")) {
                        completed.add(row.get("address").getAsString());
                    }
                }
            }
        }
        DecompInterface decompiler = new DecompInterface();
        if (!decompiler.openProgram(currentProgram)) {
            throw new IOException("Decompiler could not open the imported program");
        }
        JsonArray catalog = new JsonArray();
        FunctionIterator inventory = currentProgram.getFunctionManager().getFunctions(true);
        while (inventory.hasNext()) {
            monitor.checkCancelled();
            Function f = inventory.next();
            if (f.isExternal()) continue;
            JsonObject entry = new JsonObject();
            entry.addProperty("name", f.getName());
            entry.addProperty("address", addr(f.getEntryPoint()));
            entry.addProperty("size", f.getBody().getNumAddresses());
            catalog.add(entry);
        }
        Path inventoryPath = destination.resolveSibling("catalog.json");
        Path inventoryTemp = destination.resolveSibling("catalog.tmp");
        Files.writeString(inventoryTemp, gson.toJson(catalog), StandardCharsets.UTF_8);
        Files.move(inventoryTemp, inventoryPath, StandardCopyOption.REPLACE_EXISTING);
        try (PrintWriter writer = new PrintWriter(Files.newBufferedWriter(destination, StandardCharsets.UTF_8))) {
            JsonObject summary = new JsonObject();
            summary.addProperty("type", "summary");
            summary.addProperty("total_functions", currentProgram.getFunctionManager().getFunctionCount());
            summary.addProperty("internal_functions", catalog.size());
            summary.addProperty("image_base", addr(currentProgram.getImageBase()));
            summary.addProperty("language", currentProgram.getLanguageID().toString());
            writer.println(gson.toJson(summary)); writer.flush();
            int count = 0, skipped = 0;
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext() && count < limit) {
                monitor.checkCancelled();
                Function f = functions.next();
                if (f.isExternal()) continue;
                if (hasBase ? completed.contains(addr(f.getEntryPoint())) : skipped++ < offset) continue;
                JsonObject started = new JsonObject();
                started.addProperty("type", "started");
                started.addProperty("name", f.getName());
                started.addProperty("address", addr(f.getEntryPoint()));
                started.addProperty("time", System.currentTimeMillis() / 1000.0);
                writer.println(gson.toJson(started)); writer.flush();
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
                row.addProperty("time", System.currentTimeMillis() / 1000.0);
                writer.println(gson.toJson(row)); writer.flush(); count++;
            }
            JsonObject done = new JsonObject();
            done.addProperty("type", "complete"); done.addProperty("exported", count);
            writer.println(gson.toJson(done)); writer.flush();
        } finally { decompiler.dispose(); }
    }
}
