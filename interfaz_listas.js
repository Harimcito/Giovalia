export default function render({parentElement, data, setTriggerValue}) {
  const root = parentElement.querySelector(".gv-lista");
  const scroll = root.scrollTop;
  const foco = root.contains(document.activeElement) ? document.activeElement.dataset.fila : null;
  const node = (tag, text) => {
    const element = document.createElement(tag);
    element.textContent = text ?? "";
    return element;
  };
  const table = node("table");
  table.setAttribute("aria-label", data.titulo);
  const header = table.createTHead().insertRow();
  data.columnas.forEach((texto, i) => {
    const cell = node("th", texto);
    cell.scope = "col";
    cell.classList.toggle("gv-monto", data.montos.includes(i));
    header.append(cell);
  });
  const body = table.createTBody();
  for (const [id, ...valores] of data.filas) {
    const row = body.insertRow();
    row.classList.toggle("gv-activa", id === data.activa);
    valores.forEach((valor, i) => {
      const cell = row.insertCell();
      cell.classList.toggle("gv-monto", data.montos.includes(i));
      const label = node("span", data.columnas[i]);
      label.className = "gv-etiqueta";
      cell.append(label);
      if (i !== 0) {
        cell.append(node("span", valor));
        return;
      }
      const button = node("button", valor);
      button.type = "button";
      button.dataset.fila = id;
      if (id === data.activa) button.setAttribute("aria-current", "true");
      button.onclick = () => {
        const abrir = () => setTriggerValue("fila", id);
        if (!data.proteger || !window.giovaliaSolicitarNavegacion?.(abrir)) abrir();
      };
      row.onclick = (event) => {
        if (!event.target.closest("button") && !window.getSelection()?.toString()) button.click();
      };
      cell.append(button);
    });
  }
  root.classList.toggle("gv-compacta", data.compacta);
  root.classList.toggle("gv-fijar", data.fijar);
  root.replaceChildren(table);
  if (data.memoria) {
    try {
      root.scrollTop = scroll || Number(sessionStorage.getItem(data.memoria) || 0);
      root.onscroll = () => sessionStorage.setItem(data.memoria, String(root.scrollTop));
    } catch {
      root.scrollTop = scroll;
    }
  } else root.scrollTop = scroll;
  if (foco) [...root.querySelectorAll("button")].find(b => b.dataset.fila === foco)?.focus({preventScroll:true});
}
