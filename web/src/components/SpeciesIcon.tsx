import { mdiAccount, mdiCat, mdiDog } from "@mdi/js";
import type { Species } from "../api";
import Icon from "./Icon";

export const SPECIES_ICON: Record<Species, string> = { cat: mdiCat, dog: mdiDog, person: mdiAccount };

export default function SpeciesIcon({ species, size = 18 }: { species: Species; size?: number }) {
  return <Icon path={SPECIES_ICON[species]} size={size} className={`species-${species}`} />;
}
