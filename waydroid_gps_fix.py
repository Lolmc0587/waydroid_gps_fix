import os
import re
import shutil
import subprocess
from sys import argv
from typing import Optional
from xml.dom import minidom
import xml.etree.ElementTree as ET

class ImagePartitionManager:
    def __init__(self, image_path, config):
        self.image_path = image_path
        self.config = config
    def get_partition_offsets(self):
        """
        Get partition start offsets from an image file using fdisk.
        """
        result = subprocess.run(
            ["fdisk", "-l", self.image_path], capture_output=True, text=True, check=True
        )
        offsets = []
        flag = False
        for line in result.stdout.splitlines():
            try:
                parts = line.split()
                if len(parts) > 1 and flag:
                    offsets.append(int(parts[1]) * 512)  # Convert sectors to bytes
                if "Size" and "Device" in parts:
                    flag = True
            except:
                pass
        return offsets

    def mount_partition(self, source, mount_point, offset=False):
        """
        Mount a partition from an image file at the specified offset.
        """
        os.makedirs(mount_point, exist_ok=True)
        if not offset:
            subprocess.run(
                ["sudo", "mount", "-o", "rw", source, mount_point], check=True
            )
            return
        subprocess.run(
            ["sudo", "mount", "-o", f"rw,offset={offset}", source, mount_point],
            check=True,
        )

    def unmount_image(self, mount_point):
        """
        Unmount a mounted image and remove the mount directory.
        """
        try:
            subprocess.run(["sudo", "umount", mount_point], check=True)
            os.rmdir(mount_point)
            print(f"Unmounted {mount_point}")
        except Exception as e:
            print(e)
    def copy_files_with_permission(self, src_dir, dest_dir, files):
        """
        Copy specified files from src_dir to dest_dir and set appropriate permissions and ownership.
        """
        for file in files:
            src_file = os.path.join(src_dir, file)
            dest_file = os.path.join(dest_dir, file)
            dest_file_dir = os.path.dirname(dest_file)

            if not os.path.exists(dest_file_dir):
                os.makedirs(dest_file_dir)

            shutil.copy2(src_file, dest_file)
            print(f"Copied {src_file} to {dest_file}")

            # Set appropriate permissions
            os.chmod(
                dest_file, 0o755
            )  # Read, write, and execute for owner, read and execute for group and others
            print(f"Set permissions for {dest_file}")

            # Set appropriate ownership (typically root:root for system files)
            shutil.chown(dest_file, user="root", group="root")
            print(f"Set ownership for {dest_file}")

            # Set SELinux context
            subprocess.run(
                ["sudo", "chcon", "u:object_r:system_file:s0", dest_file], check=True
            )
            print(f"Set SELinux context for {dest_file}")

    def copy_files(self, src_dir, dest_dir, files):
        """
        Copy specified files from src_dir to dest_dir and set appropriate permissions.
        """
        for file in files:
            src_file = os.path.join(src_dir, file)
            dest_file = os.path.join(dest_dir, file)
            dest_file_dir = os.path.dirname(dest_file)

            if not os.path.exists(dest_file_dir):
                os.makedirs(dest_file_dir)

            shutil.copy2(src_file, dest_file)
            print(f"Copied {src_file} to {dest_file}")

            # Set appropriate permissions
            os.chmod(
                dest_file, 0o755
            )  # Read and write for owner, read for group and others
            print(f"Set permissions for {dest_file}")

    def resize(self, filename):
        img_size = int(os.path.getsize(filename) / (1024 * 1024))
        new_size = "{}M".format(img_size + 100)
        print("Resizing image to {}...".format(new_size))
        self.run(
            ["sudo", "e2fsck", "-y", "-f", filename], ignore=r"^e2fsck \d+\.\d+\.\d (.+)\n$"
        )
        self.run(
            ["sudo", "resize2fs", filename, new_size],
            ignore=r"^resize2fs \d+\.\d+\.\d (.+)\n$",
        )

    def run(self, args: list, env: Optional[str] = None, ignore: Optional[str] = None):
        result = subprocess.run(
            args=args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        if result.stderr:
            error = result.stderr.decode("utf-8")
            if ignore and re.match(ignore, error):
                return result
            raise subprocess.CalledProcessError(
                returncode=result.returncode, cmd=result.args, stderr=result.stderr
            )
        return result

    def update_manifest(self, manifest_path, data):
        """
        Update the manifest file by inserting data into the <manifest> tag and format the XML.
        """
        tree = ET.parse(manifest_path)
        root = tree.getroot()

        # Insert the data into the root <manifest> tag
        root.append(ET.fromstring(data))

        # Write the updated XML back to the file
        tree.write(manifest_path, encoding="utf-8", xml_declaration=True)

        # Read and format the updated content of the file
        with open(manifest_path, "r") as f:
            content = f.read()
            formatted_content = minidom.parseString(content).toprettyxml(indent="    ")

        # Remove extra newlines
        formatted_content = "\n".join(
            [line for line in formatted_content.split("\n") if line.strip()]
        )

        # Write the formatted content back to the file
        with open(manifest_path, "w") as f:
            f.write(formatted_content)

    def update_build_prop(self, build_prop_path):
        """
        Append GPS configuration properties to the build.prop file.ImagePartitionManager
        """
        with open(build_prop_path, "a") as f:
            f.write('\nro.factory.hasGPS=true\n')
            f.write(f'ro.kernel.android.gps={self.config["usb_host"]}\n')
            f.write(f'ro.kernel.android.gps.speed={self.config["usb_baud_rate"]}\n')
            print(f"Updated {build_prop_path}")

    def bind_usb_device(self):
        """
        Bind the USB serial GPS device to Waydroid's LXC container.
        """
        config_nodes = "/var/lib/waydroid/lxc/waydroid/config_nodes"
        with open(config_nodes, "a") as f:
            f.write(f'lxc.mount.entry = /dev/{self.config["usb_host"]} dev/{self.config["usb_host"]} none bind,create=file,optional 0 0\n')
            print(f"Updated {config_nodes}")
        # subprocess.run(["sudo", "chmod", "666", f"/dev/{self.config["usb_target"]}"], check=True)
        # print(f"Set permissions for /dev/{self.config["usb_target"]}")

class GPSImagePatcher:
    def __init__(self, reference_image, target_image_vendor, target_image_system, image_type="lineage", config={}):
        self.reference_image = reference_image
        self.image_type = image_type
        self.target_image_vendor = target_image_vendor
        self.target_image_system = target_image_system
        self.ref_mount = "/mnt/reference_image"
        self.target_mount_vendor = "/mnt/waydroid_vendor"
        self.target_mount_system = "/mnt/waydroid_system"
        self.manager = ImagePartitionManager(reference_image, config)
        
        self.image_source_config = {
            "bliss": {
                "vendor_path_source": os.path.join(self.ref_mount, "system", "vendor"),
                "vendor_path_target": self.target_mount_vendor,
                "system_path_source": os.path.join(self.ref_mount, "system"),
                "system_path_target": os.path.join(self.target_mount_system, "system"),
                "system_files": {
                    "system_target_files": [
                        "lib/hw/gps.default.so",
                        "lib64/hw/gps.default.so",
                    ],
                    "vendor_target_files": []
                },
                "vendor_files": {
                    "system_target_files": [ # all file left need to copy to system partition
                        "lib/hw/android.hardware.gnss@1.0-impl.so",
                        "lib64/hw/android.hardware.gnss@1.0-impl.so",
                        "etc/init/android.hardware.gnss@1.0-service.rc",
                    ],
                    "vendor_target_files": [
                        "bin/hw/android.hardware.gnss@1.0-service",
                    ] # this file need to copy to vendor partition
                }
            },
            "lineage": {
                "vendor_path_source": self.ref_mount,
                "vendor_path_target": self.target_mount_vendor,
                "system_path_source": self.ref_mount,
                "system_path_target": os.path.join(self.target_mount_system, "system"),
                "vendor_files": { # copy from source vendor to waydroid 
                    "vendor_target_files": [
                        "bin/hw/android.hardware.gnss@1.0-service",
                    ],
                    "system_target_files": []
                },
                "system_files": {
                    "system_target_files": [
                        "etc/init/android.hardware.gnss@1.0-service.rc",
                        "lib/hw/android.hardware.gnss@1.0-impl.so",
                        "lib64/hw/android.hardware.gnss@1.0-impl.so",
                        "lib/hw/gps.default.so",
                        "lib64/hw/gps.default.so",
                    ],
                    "vendor_target_files": []
                }
            },
        }
    def patch_images(self):
        file_umount = []
        try:
            if self.image_type == "lineage":
                # Get partition offsets and mount all partitions
                ref_offsets = self.manager.get_partition_offsets()
                if not ref_offsets:
                    raise ValueError("Could not determine partition offsets.")

                # Mount vendor partition
                for offset in ref_offsets:
                    self.manager.mount_partition(self.reference_image, self.ref_mount, offset)
                    if os.path.exists(f"{self.ref_mount}/lib64"): # find vendor partition on rpi4 image
                        print("Mounted ref vendor partition")
                        break
                    self.manager.unmount_image(self.ref_mount)
                file_umount.append(self.ref_mount)
            if self.image_type == "bliss":
                main_image_path = "/mnt/main_image"
                main_sfs_path = "/mnt/sfs"

                sfs_image_path = os.path.join(main_image_path, "system.sfs")
                img_system_path = os.path.join(main_sfs_path, "system.img")
                # target_img_path = os.path.join(target_sfs_path, "sys")
                
                # system_vendor_path = os.path.join(main_image_path, "system.img")
                
                self.manager.mount_partition(self.reference_image, main_image_path, 0)
                self.manager.mount_partition(sfs_image_path, main_sfs_path, 0)
                self.manager.mount_partition(img_system_path, self.ref_mount, False)
                file_umount.extend([self.ref_mount, main_sfs_path, main_image_path])
                print("Mounted system/vendor partition")
                pass
            # exit()
            # Resize target images to accommodate additional files
            self.manager.resize(target_image_vendor)
            self.manager.resize(target_image_system)

            self.manager.mount_partition(self.target_image_vendor, self.target_mount_vendor, False)
            self.manager.mount_partition(self.target_image_system, self.target_mount_system, False)
            
            file_umount.extend([self.target_mount_vendor, self.target_mount_system])

            # Define source vendor path and list of necessary files
            vendor_path_source = self.image_source_config[self.image_type]["vendor_path_source"]
            vendor_path_target = self.image_source_config[self.image_type]["vendor_path_target"]

            # Define source system path and list of necessary files
            system_path_source = self.image_source_config[self.image_type]["system_path_source"]
            system_path_target = self.image_source_config[self.image_type]["system_path_target"]

            # Define source vendor path and list of necessary files
            vendor_copy = self.image_source_config[self.image_type]["vendor_files"]
            system_copy = self.image_source_config[self.image_type]["system_files"]
            # Copy required GNSS and GPS files from vendor source to target vendor partition
            self.manager.copy_files_with_permission(vendor_path_source, vendor_path_target, vendor_copy["vendor_target_files"])

            # Copy required GNSS and GPS files from system source to target vendor partition
            self.manager.copy_files_with_permission(system_path_source, vendor_path_target, system_copy["vendor_target_files"])
            print("Copied files to target vendor partition")


            # Copy required GNSS and GPS files from system to target system partition
            self.manager.copy_files_with_permission(system_path_source, system_path_target, system_copy["system_target_files"])

            # Copy required GNSS and GPS files from vendor to target system partition
            self.manager.copy_files_with_permission(vendor_path_source, system_path_target, vendor_copy["system_target_files"])
            print("Copied files to target system partition")

            # # Update manifest and build properties
            manifest_entry_1 = """
                <hal format="hidl" optional="true">
                    <name>android.hardware.gnss</name>
                    <version>1.0</version>
                    <interface>
                        <name>IGnss</name>
                        <instance>default</instance>
                    </interface>
                </hal>
            """
            self.manager.update_manifest(
                f"{system_path_target}/etc/vintf/compatibility_matrix.legacy.xml",
                data=manifest_entry_1,
            )
            print("Updated compatibility_matrix.legacy.xml file")

            manifest_entry_2 = """
                <hal format="hidl">
                    <name>android.hardware.gnss</name>
                    <transport>hwbinder</transport>
                    <version>1.0</version>
                    <interface>
                        <name>IGnss</name>
                        <instance>default</instance>
                    </interface>
                    <fqname>@1.0::IGnss/default</fqname>
                </hal>
            """
            self.manager.update_manifest(f"{system_path_target}/etc/vintf/manifest.xml", data=manifest_entry_2)
            print("Updated manifest.xml file")
            self.manager.update_build_prop(f"{system_path_target}/build.prop")
            print("Updated build.prop file")
        finally:
            # Unmount both images after modifications
            for img in file_umount:
                self.manager.unmount_image(img)
            pass
        
        self.manager.bind_usb_device()
        print("GPS/GNSS support enabled in Waydroid!")


if __name__ == "__main__":
    #reference_image = "<Your img source>"
    reference_image = argv[1]
    target_image_vendor = "/var/lib/waydroid/images/vendor.img"
    target_image_system = "/var/lib/waydroid/images/system.img"
    image_type = "lineage"
    config = {
        "usb_host": "ttyGPSD",
        "usb_baud_rate": 57600,
    }
    # source image: "bliss" for x86_64 img, "lineage" for arm64 img
    patcher = GPSImagePatcher(reference_image, target_image_vendor, target_image_system, image_type=image_type, config=config)
    patcher.patch_images()
    # patcher.bind_usb_device()