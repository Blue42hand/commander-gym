import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

/** Offline probe of the same existing-file POSIX lock used by native capture. */
public class HistoricalLockProbe {
    public static void main(String[] args) throws Exception {
        try (FileChannel channel = FileChannel.open(Path.of(args[0]), StandardOpenOption.WRITE)) {
            FileLock lock = channel.tryLock();
            System.out.println(lock == null ? "BUSY" : "LOCKED");
            System.out.flush();
            if (lock != null) {
                if (args[1].equals("hold")) System.in.read();
                lock.release();
            }
        }
    }
}
