import os
import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox
from openpyxl.utils import get_column_letter


def seleccionar_csv():
    root = tk.Tk()
    root.withdraw()

    archivo_csv = filedialog.askopenfilename(
        title="Selecciona el archivo CSV",
        filetypes=[
            ("Archivos CSV", "*.csv"),
            ("Todos los archivos", "*.*")
        ]
    )

    if not archivo_csv:
        print("No se seleccionó ningún archivo.")
        return

    convertir_a_excel(archivo_csv)

    root.destroy()


def convertir_a_excel(archivo_csv):
    try:
        # Intentar leer como UTF-8
        try:
            df = pd.read_csv(
                archivo_csv,
                encoding="utf-8-sig"
            )
        except UnicodeDecodeError:
            # Por si el CSV viene con codificación de Windows
            df = pd.read_csv(
                archivo_csv,
                encoding="latin-1"
            )

        # Crear nombre del Excel
        archivo_excel = os.path.splitext(archivo_csv)[0] + ".xlsx"

        # Guardar Excel
        with pd.ExcelWriter(
            archivo_excel,
            engine="openpyxl"
        ) as writer:

            df.to_excel(
                writer,
                sheet_name="Datos",
                index=False
            )

            ws = writer.sheets["Datos"]

            # Congelar encabezado
            ws.freeze_panes = "A2"

            # Activar filtros
            ws.auto_filter.ref = ws.dimensions

            # Ajustar ancho de columnas automáticamente
            for columna in ws.columns:
                longitud_maxima = 0
                letra_columna = get_column_letter(columna[0].column)

                for celda in columna:
                    if celda.value is not None:
                        longitud = len(str(celda.value))
                        longitud_maxima = max(
                            longitud_maxima,
                            longitud
                        )

                # Máximo de 50 para evitar columnas gigantes
                ancho = min(longitud_maxima + 2, 50)
                ws.column_dimensions[letra_columna].width = ancho

        print("\n✓ Conversión completada")
        print(f"CSV:   {archivo_csv}")
        print(f"Excel: {archivo_excel}")

        messagebox.showinfo(
            "Conversión completada",
            f"Archivo Excel creado correctamente:\n\n{archivo_excel}"
        )

    except Exception as e:
        print(f"ERROR: {e}")

        messagebox.showerror(
            "Error",
            f"No se pudo convertir el archivo:\n\n{e}"
        )


if __name__ == "__main__":
    seleccionar_csv()