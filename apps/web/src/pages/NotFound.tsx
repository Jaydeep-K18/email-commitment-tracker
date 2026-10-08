import { Compass } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

import { Button } from "../components/ui/button";
import { EmptyState } from "../components/ui/states";

export default function NotFound() {
  const location = useLocation();
  return (
    <EmptyState
      className="py-24"
      icon={<Compass />}
      title="There's no page here"
      description={<>Nothing lives at <span className="font-mono text-text">{location.pathname}</span>. It may have moved, or the link was mistyped.</>}
      action={<Link to="/"><Button variant="primary">Back to overview</Button></Link>}
    />
  );
}
